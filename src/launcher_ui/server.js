/* Окно локального сервера: следит за журналом и управляет процессом. */

"use strict";

const $ = (id) => document.getElementById(id);

const ui = {
  running: true,
  port: 0,
  url: "",
  host: "",
};

let ready = false;

function onReady() {
  if (ready) return;
  const api = window.pywebview && window.pywebview.api;
  if (!api || !api.get_state) {
    setTimeout(onReady, 150);
    return;
  }
  ready = true;
  api.get_state().then(function (s) {
    apply_state(s);
    poll();          // дальше журнал обновляется сам
  }).catch(show_fatal);
}

function api() {
  return window.pywebview && window.pywebview.api;
}

function show_fatal(e) {
  $("err").hidden = false;
  $("err").textContent = "Связь с программой потеряна: " +
    String(e && e.message ? e.message : e);
}

/** Показать адрес, по которому подключаются остальные. */
window.set_addr = function (host, port) {
  $("addrLine").textContent =
    "Другие подключаются по адресу:  https://" + host + ":" + port;
};

function apply_state(s) {
  ui.port = s.port;
  ui.url = s.url;
  ui.host = s.host;
  ui.running = s.running;
  window.set_addr(s.host, s.port);
  $("since").textContent = s.startedAt
    ? "запущен в " + s.startedAt : "";
  $("btnStop").disabled = !s.running;
  $("btnRestart").disabled = !s.running && !s.canStart;
  show_update(s.update);
}

/** Показать, что новая версия уже скачана и ждёт перезапуска. */
function show_update(u) {
  const line = $("updLine");
  if (!u || !u.pending) {
    line.hidden = true;
    return;
  }
  line.hidden = false;
  line.textContent = "Обновление " + (u.from || "?") + " → " + (u.to || "?") +
    " скачано. Сервер сам перезапустится с ним в 3:00 ночи — или нажмите" +
    " «Перезапустить».";
}

window.set_state = function (running, note) {
  ui.running = running;
  $("dot").className = "dot" + (running ? "" : " is-off");
  $("stateText").textContent = running ? "Работает" : "Остановлен";
  $("btnStop").disabled = !running;
  $("btnStop").textContent = running ? "Остановить" : "Запустить снова";
  if (note) $("since").textContent = note;
};

window.log_text = function (text) {
  const box = $("logBox");
  // не трогаем содержимое, если не изменилось: иначе текст дёргается
  // и теряется выделение, если пользователь что-то ищал глазами
  if (box.textContent === text) return;
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 24;
  box.textContent = text;
  if (atBottom) box.scrollTop = box.scrollHeight;
};

window.log_append = function (line) {
  const box = $("logBox");
  box.textContent += line + "\n";
  box.scrollTop = box.scrollHeight;
};

/* Журнал живой: сервер дописывает в файл уже после открытия окна, поэтому
   один раз при загрузке показывал бы пустоту. Спрашиваем сами — так заодно
   видно, если сервер упал: точка гаснет, кнопка снова «Запустить снова». */
function poll() {
  if (!ready) return;
  api().poll().then(function (s) {
    if (!ready) return;
    window.set_state(s.running, s.startedAt ? "запущен в " + s.startedAt : "");
    window.log_text(s.log);
    show_update(s.update);
  }).catch(function () {
    /* окно закрывается — молча заканчиваем опрос */
    ready = false;
  }).finally(function () {
    setTimeout(poll, 2000);
  });
}

$("btnBrowser").onclick = () => api().open_browser();
$("btnStop").onclick = () => api().toggle_server();
$("btnRestart").onclick = () => api().restart_server();

/**
 * Разрешить входящие подключения.
 *
 * Правила брандмауэра меняет только тот, у кого есть права, — поэтому
 * при нехватке прав говорим об этом прямо и подсказываем, что делать, а не
 * молчим. Иначе человек нажмёт, ничего не заметит и решит, что не помогло.
 */
$("btnFirewall").onclick = async () => {
  const btn = $("btnFirewall");
  const err = $("err");
  btn.disabled = true;
  const was = btn.textContent;
  btn.textContent = "Разрешаю…";
  try {
    const r = await api().allow_firewall();
    if (r && r.ok) {
      err.hidden = true;
      btn.textContent = "Разрешено";
    } else if (r && r.needsAdmin) {
      err.hidden = false;
      err.textContent =
        "Нужны права администратора. Закройте программу, запустите "
        + "NetSpeakServer.exe правой кнопкой → «Запуск от имени "
        + "администратора» и повторите.";
    } else {
      err.hidden = false;
      err.textContent = "Не получилось: " + ((r && r.error) || "неизвестно");
    }
  } catch (e) {
    err.hidden = false;
    err.textContent = "Не получилось: " + e;
  } finally {
    setTimeout(() => {
      btn.textContent = was;
      btn.disabled = false;
    }, 2600);
  }
};

window.addEventListener("pywebviewready", onReady);
if (window.pywebview && window.pywebview.api) onReady();