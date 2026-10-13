/* Клиент: спрашиваем адрес сервера и подключаемся. */

"use strict";

const $ = (id) => document.getElementById(id);

const ui = { own: [] };

let ready = false;

function onReady() {
  if (ready) return;
  const api = window.pywebview && window.pywebview.api;
  if (!api || !api.get_state) {
    setTimeout(onReady, 150);   // мост появится чуть позже
    return;
  }
  ready = true;
  api.get_state().then(fill_state).catch(show_fatal);
}

function api() {
  return window.pywebview && window.pywebview.api;
}

function show_fatal(e) {
  $("joinErr").hidden = false;
  $("joinErr").textContent = "Связь с программой потеряна: " +
    String(e && e.message ? e.message : e);
}

function fill_state(s) {
  $("rootPath").textContent = s.root;
  $("hostIn").value = (s.lastServer && s.lastServer.host) || s.defaultAddress;
  $("portIn2").value = (s.lastServer && s.lastServer.port) || s.defaultPort;
  ui.own = s.ownAddresses || [];
  if (s.lastServer && s.lastServer.host) {
    $("lastHint").hidden = false;
    $("lastHint").textContent =
      "Прошлый адрес: " + s.lastServer.host + ":" + s.lastServer.port;
  }
}

function fail(text) {
  $("joinErr").hidden = false;
  $("joinErr").textContent = text;
}

$("btnGo").onclick = async () => {
  const h = $("hostIn").value.trim()
    .replace(/^https?:\/\//, "").replace(/\/.*$/, "");
  const p = Number($("portIn2").value);
  $("joinErr").hidden = true;

  if (!h) return fail("Впишите адрес сервера");
  if (!Number.isInteger(p) || p < 2 || p > 65535) {
    return fail("Порт должен быть числом от 2 до 65535");
  }
  $("btnGo").disabled = true;
  $("btnGo").textContent = "Подключаемся…";

  const ok = await api().connect(h, p).catch((e) => {
    fail(String(e));
    return false;
  });
  if (ok === false) {
    fail("По адресу " + h + ":" + p + " никто не отвечает.\n" +
         "Проверьте, что сервер запущен и порт совпадает.");
  }
  $("btnGo").disabled = false;
  $("btnGo").textContent = "Подключиться";
};

$("hostIn").onkeydown = (e) => { if (e.key === "Enter") $("btnGo").click(); };
$("portIn2").onkeydown = (e) => { if (e.key === "Enter") $("btnGo").click(); };

window.addEventListener("pywebviewready", onReady);
if (window.pywebview && window.pywebview.api) onReady();