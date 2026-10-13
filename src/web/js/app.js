/* NetSpeak — клиент: вход, каналы, чат, список участников. */

import { Voice } from "./voice.js";

const $ = (id) => document.getElementById(id);
const els = {
  login: $("login"), loginForm: $("loginForm"), loginName: $("loginName"),
  loginChannel: $("loginChannel"), loginPwWrap: $("loginPwWrap"),
  loginPass: $("loginPass"), loginBtn: $("loginBtn"), loginError: $("loginError"),
  loginAddr: $("loginAddr"),
  app: $("app"), sidebar: $("sidebar"), scrim: $("scrim"),
  btnChannels: $("btnChannels"), closeSidebar: $("closeSidebar"),
  channelList: $("channelList"), serverName: $("serverName"),
  pingPill: $("pingPill"), slotsPill: $("slotsPill"), btnSettings: $("btnSettings"),
  slotsLine: $("slotsLine"), btnTheme: $("btnTheme"), themeIcon: $("themeIcon"),
  btnThemeLogin: $("btnThemeLogin"), themeIconLogin: $("themeIconLogin"),
  btnSettingsLogin: $("btnSettingsLogin"),
  micWarn: $("micWarn"), pttKey: $("pttKey"),
  videos: $("videos"), tileGrid: $("tileGrid"), stageEmpty: $("stageEmpty"),
  chatLog: $("chatLog"), chatForm: $("chatForm"), chatText: $("chatText"),
  attachBtn: $("attachBtn"), fileInput: $("fileInput"),
  chatTitle: $("chatTitle"), chatHint: $("chatHint"),
  voiceWarn: $("voiceWarn"), voiceWarnTitle: $("voiceWarnTitle"),
  voiceWarnText: $("voiceWarnText"), voiceWarnFix: $("voiceWarnFix"),
  voiceWarnClose: $("voiceWarnClose"),
  btnSoundCheck: $("btnSoundCheck"), soundCheckOut: $("soundCheckOut"),
  membersList: $("membersList"), membersCount: $("membersCount"),
  micBtn: $("micBtn"), micLabel: $("micLabel"), deafenBtn: $("deafenBtn"),
  leaveBtn: $("leaveBtn"), pttHint: $("pttHint"),
  settingsModal: $("settingsModal"), closeSettings: $("closeSettings"),
  saveSettings: $("saveSettings"), modeSegment: $("modeSegment"),
  keyField: $("keyField"), micDevice: $("micDevice"),
  micHotkey: $("micHotkey"), hotkeyField: $("hotkeyField"),
  micStatus: $("micStatus"), micStatusRows: $("micStatusRows"),
  micMeterCut: $("micMeterCut"), micMeterLoud: $("micMeterLoud"), micMeterGate: $("micMeterGate"),
  highpass: $("highpass"), highpassValue: $("highpassValue"),
  gate: $("gate"), gateValue: $("gateValue"),
  leveling: $("leveling"), levelingValue: $("levelingValue"),
  speakerDevice: $("speakerDevice"), micGain: $("micGain"),
  gainValue: $("gainValue"), micMeter: $("micMeter"),
  optEcho: $("optEcho"), optNoise: $("optNoise"), optAgc: $("optAgc"),
  // профиль
  profileName: $("profileName"), showCircle: $("showCircle"),
  avatarPick: $("avatarPick"), avatarPickBtn: $("avatarPickBtn"),
  avatarClearBtn: $("avatarClearBtn"), avatarInput: $("avatarInput"),
  avatarImg: $("avatarImg"), avatarCircle: $("avatarCircle"),
  toast: $("toast"),
};

/**
 * Идентификатор вкладки.
 * crypto.randomUUID() доступен только в защищённом контексте (HTTPS или
 * localhost), а мы часто открываем NetSpeak по обычному HTTP из локальной сети —
 * поэтому берём getRandomValues, который работать всегда.
 */
function newSid() {
  const buf = new Uint8Array(8);
  if (window.crypto?.getRandomValues) {
    crypto.getRandomValues(buf);
  } else {
    for (let i = 0; i < buf.length; i++) buf[i] = Math.floor(Math.random() * 256);
  }
  return Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
}

/**
 * Постоянный идентификатор человека, а не вкладки.
 *
 * sid живёт в sessionStorage и умирает вместе с окном — каждый запуск
 * даёт новый, и прошлые сообщения в сохранённой переписке переставали
 * узнаваться: они выравнивались влево и теряли аватар. uid лежит в
 * localStorage, поэтому переживает перезапуск и переносится вместе с
 * папкой пользователя браузера.
 *
 * Именно его пользователь назвал «хэш-id для приложения»: он и отвечает
 * на вопрос «это тот же самый человек?».
 */
function newUid() {
  const buf = new Uint8Array(8);
  if (window.crypto?.getRandomValues) {
    crypto.getRandomValues(buf);
  } else {
    for (let i = 0; i < buf.length; i++) buf[i] = Math.floor(Math.random() * 256);
  }
  return Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Что мы знаем о том, откуда запущено окно. */
const device = {
  machine: "",     // номер компьютера; пусто, если мы пришли по сети
  seed: "",        // случайное число этой копии программы
  uid: "",         // номер человека, посчитанный на устройстве
  sameMachine: false,
  name: "",        // как называется этот компьютер
};

/**
 * Спрашивает у сервера номер устройства — но только если мы на нём же.
 *
 * Открыли страницу по сети с телефона или чужого ноутбука: серверный номер
 * компьютера тут ни при чём, и брать его нельзя — иначе все, кто зашёл
 * через одну ссылку, оказались бы одним человеком. В этом случае device
 * остаётся пустым, и номер считается только по хранилищу браузера.
 */
async function loadDevice() {
  const host = location.hostname;
  const own = !host || host === "127.0.0.1" || host === "localhost"
                 || host === "::1" || host === "[::1]";
  if (!own) return;
  try {
    const r = await fetch("/api/device", { cache: "no-store" });
    if (!r.ok) return;
    const d = await r.json();
    if (d && d.sameMachine && d.machine) {
      device.machine = d.machine;
      device.seed = d.seed || "";
      device.uid = d.uid || "";
      device.sameMachine = true;
      device.name = d.name || "";
    }
  } catch { /* сервер не ответил — работаем по-старому */ }
}

/**
 * Номер человека.
 *
 * Прежде он лежал в хранилище браузера, и это доставляло сюрпризы: стоило
 * перенести папку программы на другой компьютер — и человек становился
 * тем же самым, с тем же именем и тем же аватаром, а сервер показывал
 * обоих одинаковыми.
 *
 * Теперь номер выводится из двух частей:
 *   — номер компьютера (тот же ноутбук под другим углом — тот же человек);
 *   — зёрныше этой копии программы (две копии на одном компьютере — разные).
 * Перенесли папку на другой компьютер — зёрныше переехало, номер машины
 * нет, и человек получился другой. Это ровно то, чего не хватало.
 */
async function mixUid() {
  // На своём компьютере номер считает сервер: из номера машины и зёрныша
  // этой копии программы, без случайной части. Так он переживает любую
  // потерю настроек браузера — потерял папку, запустил снова, получил тот
  // же номер, и сервер вернул ему прежнее имя с аватаром.
  if (device.sameMachine && device.uid) return device.uid;

  // Пришли по сети с телефона или чужого ноутбука: номера устройства у нас
  // нет и взять его неоткуда. Тогда номер живёт в самом браузере — так и
  // было раньше, и для этого случая годится.
  let uid = localStorage.getItem("ns.uid");
  if (uid && /^[0-9a-f]{16}$/.test(uid)) return uid;
  uid = newUid();
  localStorage.setItem("ns.uid", uid);
  return uid;
}

/** Всё про «кто я» — до того, как начнём входить. */
async function initIdentity() {
  await loadDevice();
  state.uid = await mixUid();
  state.device = device;
  localStorage.setItem("ns.uid", state.uid);
  // список людей нужен, чтобы восстановить своё имя из базы сервера
  try { await loadInfo(); } catch { /* сервер не ответил */ }
}

/** Моё ли это сообщение: по соединению или по постоянному id. */
function isMine(m) {
  if (!m) return false;
  if (m.from === state.sid) return true;
  return !!state.uid && m.uid === state.uid;
}

// Выбор микрофона и динамиков НЕ хранится на сервере. Номер устройства
// привязан к компьютеру: на ноутбуке и на настольном он разный, и сохранённый
// на одном на другом не существует. Браузер на такой просьбе отвечает отказом —
// микрофон не открывается вовсе, и человека не слышно. Всё остальное
// по-прежнему хранится на сервере, чтобы настройки переживали смену браузера.
const LOCAL_ONLY_SETTINGS = ["mic", "speaker"];

const savedSettings = JSON.parse(localStorage.getItem("ns.settings") || "{}");
// режима «Кнопкой» больше нет — переводим старые настройки на актуальные
if (savedSettings.mode === "toggle") savedSettings.mode = "ptt";
// Ctrl+Пробел мешал в играх, меняем на удобное сочетание
if (!savedSettings.hotkey || savedSettings.hotkey === "Control+Space") {
  savedSettings.hotkey = "ControlRight+Insert";
}

// Настройки по умолчанию. Те же значения лежат в settings/app.json на сервере —
// серверные перекрывают сохранённые в браузере, поэтому работать можно и без
// доступа к диску (например, если страницу открыли с чужого компьютера).
const DEFAULT_SETTINGS = {
  theme: "dark",
  mode: "on",
  key: "Space",
  hotkey: "ControlRight+Insert",
  mic: "",
  speaker: "",
  gain: 100,
  highpass: 70,
  gate: 6,
  leveling: 40,
  echo: true, noise: true, agc: true,
};

const state = {
  // sessionStorage, а не localStorage: каждая вкладка — отдельный участник
  sid: sessionStorage.getItem("ns.sid") || newSid(),
  // а вот это — сам человек, и оно переживает перезапуск
  uid: localStorage.getItem("ns.uid") || newUid(),
  name: localStorage.getItem("ns.name") || "",
  channels: [],
  users: new Map(),
  // люди, которые бывали на сервере: uid → {name, avatar}. Отсюда берутся
  // чужие аватары и имена, в том числе у тех, кто сейчас не в сети
  people: new Map(),
  me: null,
  channel: null,
  maxUsers: 8,
  dm: null,
  speaking: new Set(),
  sharedVideo: new Map(),
  source: null,
  ping: 0,
  // профиль живёт в user/profile.json на сервере
  profile: { name: "", avatar: "", avatarUrl: "", showCircle: true },
  settings: Object.assign({}, DEFAULT_SETTINGS, savedSettings),
};

/* ── профиль и настройки на диске ──
   Сервер хранит их в user/profile.json и settings/app.json рядом с собой.
   Если сервер их не отдаёт (страница открыта не через NetSpeak) — работаем
   на localStorage, чтобы интерфейс оставался живым. */

let storeApi = true; // доступен ли файловый сервер
let profileReady = Promise.resolve(); // чем дожидаться загрузки профиля

/** Подменяет данные своими: профиль у нас свежее, чем то, что прислал сервер. */
function withMyProfile(u) {
  if (!u || u.id !== state.sid) return u;
  return {
    ...u,
    avatar: state.profile.avatarUrl || u.avatar,
    showCircle: state.profile.showCircle,
  };
}

/** Данные участника: сперва из базы сервера, потом из списка в сети. */
function memberView(u) {
  if (!u) return u;
  const known = profileOf(u.uid);
  if (!known || u.id === state.sid) return u;
  return {
    ...u,
    name: u.name || known.name,
    avatar: u.avatar || known.avatarUrl,
    color: u.color || known.color,
    showCircle: u.showCircle !== false && known.showCircle,
  };
}

async function loadFromServer() {
  try {
    const [prof, app] = await Promise.all([
      fetch("/api/profile", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)),
      fetch("/api/appsettings", { cache: "no-store" }).then((r) => (r.ok ? r.json() : null)),
    ]);
    if (!prof && !app) {
      storeApi = false;
      return;
    }
    storeApi = true;

    // Кто я. Раньше здесь без разбора брался профиль сервера, и это было
    // прямой ошибкой: файл один на всех, поэтому подключившийся с ноутбука
    // получал имя и аватар того, кто запустил сервер. Теперь выбор
    // упорядочен, и каждый шаг объясним.
    let mine = loadLocalProfile(state.uid);

    // 1. моё имя и мой аватар на этом устройстве — что я выбрал сам
    // 2. запись в базе сервера с МОИМ номером: вернуть имя, если браузер
    //    забыли (перенесли папку настроек, почистили данные)
    if (!mine?.name) {
      const rec = state.people.get(state.uid);
      if (rec?.name) {
        mine = { name: rec.name, avatar: rec.avatar || "", showCircle: rec.showCircle !== false };
      }
    }
    // 3. профиль серверной машины — только мы на ней, и только в самый
    //    первый раз. Дальше своё имя уже есть, и чужие его не подменяют.
    if (!mine?.name && device.sameMachine && prof?.name) {
      mine = { name: prof.name, avatar: prof.avatar || "", showCircle: prof.showCircle !== false };
    }

    state.profile = {
      name: mine?.name || "",
      avatar: mine?.avatar || "",
      avatarUrl: mine?.avatar ? avatarLink(mine.avatar) : "",
      showCircle: mine?.showCircle !== false,
    };
    if (state.profile.name) {
      state.name = state.profile.name;
      localStorage.setItem("ns.name", state.name);
      saveLocalProfile(state.uid, state.profile);
    }
    if (app) {
      // серверные настройки главнее браузерных: они переживают смену браузера
      state.settings = Object.assign({}, DEFAULT_SETTINGS, app);
      // устройства — из этого браузера, а не с сервера: см. LOCAL_ONLY_SETTINGS
      for (const key of LOCAL_ONLY_SETTINGS) {
        state.settings[key] = savedSettings[key] || "";
      }
      applyTheme(state.settings.theme);
    }
  } catch {
    storeApi = false;
  }
}

/** Ссылка на аватар по имени файла. */
function avatarLink(name) {
  return name && name.startsWith("/") ? name : (name ? "/avatars/" + encodeURIComponent(name) : "");
}

/** Мой профиль на этом устройстве: именем и аватаром владею я. */
function loadLocalProfile(uid) {
  try {
    const raw = localStorage.getItem("ns.profile." + uid);
    const p = raw ? JSON.parse(raw) : null;
    return p && typeof p === "object" ? p : null;
  } catch { return null; }
}

function saveLocalProfile(uid, profile) {
  try {
    localStorage.setItem("ns.profile." + uid, JSON.stringify({
      name: profile.name || "",
      avatar: profile.avatar || "",
      showCircle: profile.showCircle !== false,
    }));
  } catch { /* без хранилища никуда */ }
}

async function saveProfile(patch) {
  Object.assign(state.profile, patch);
  if (state.profile.avatar && !state.profile.avatar.startsWith("/")) {
    state.profile.avatarUrl = avatarLink(state.profile.avatar);
  }
  // сперва у себя: чтобы профиль не мигал до ответа сервера и жил
  // именно под этим номером, а не под чужим
  if (state.profile.name) {
    localStorage.setItem("ns.name", state.profile.name);
    saveLocalProfile(state.uid, state.profile);
  }
  if (!storeApi) return;
  try {
    await fetch("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...state.profile, uid: state.uid }),
    });
  } catch { /* сервер пропал — останется как есть в браузере */ }
}

async function saveAppSettings() {
  localStorage.setItem("ns.settings", JSON.stringify(state.settings));
  if (!storeApi) return;
  try {
    // выбранные устройства на сервер не отправляем: они относятся к этому
    // компьютеру, и на чужом такой номер всё равно не работает
    const shared = { ...state.settings };
    for (const key of LOCAL_ONLY_SETTINGS) delete shared[key];
    await fetch("/api/appsettings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(shared),
    });
  } catch { /* см. выше */ }
}

/** Сообщает серверу, что аватар или кружок поменялись, — их видят другие. */
function pushProfileToServer() {
  send({
    type: "update",
    uid: state.uid,
    // имя тоже: сервер держит его в базе, и без него переименование
    // пропало бы после выхода из сети. Аватар не отправляем — он и так
    // лежит в базе, а сервер возьмёт его сам по нашему номеру.
    name: state.profile.name || "",
    avatar: "",
    showCircle: state.profile.showCircle,
  }).catch(() => {});
}

/** Переносит настройки в звуковой движок. Без этого микрофон поднимается
 *  с заводскими значениями, и сохранённые настройки молча не работают. */
function syncSettingsToVoice() {
  Object.assign(voice.settings, {
    echo: state.settings.echo,
    noise: state.settings.noise,
    autoGain: state.settings.agc,
    gain: state.settings.gain,
    highpass: state.settings.highpass,
    gate: state.settings.gate,
    leveling: state.settings.leveling,
    inputDevice: state.settings.mic,
    outputDevice: state.settings.speaker,
  });
  voice._applyAudioSettings();
}

/* ─────────────────── сеть ─────────────────── */

async function api(path, body) {
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    // Сервер забыл нашу сессию: сел в сон, потерял связь или был перезапущен.
    // Раньше здесь была перезагрузка страницы — человек вылетал на окно входа,
    // будто приложение закрылось. Теперь просто переподключаемся: имя и
    // канал у нас уже есть, терять ничего не нужно.
    rejoin();
    throw new Error("session expired");
  }
  return res.json();
}

/** Переподключение без перезагрузки. Несколько запросов могут упасть
 *  одновременно, поэтому защищены от повтора. */
let rejoining = false;
function rejoin() {
  if (rejoining) return;
  rejoining = true;
  toast("Связь потеряна, переподключаемся…", "warn");
  state.source?.close();
  state.source = null;
  setTimeout(() => {
    rejoining = false;
    connect();
    pingLoop();
  }, 1200);
}

function send(payload) {
  return api("/api/send", Object.assign({ sid: state.sid }, payload));
}

async function loadInfo() {
  const info = await api("/api/info");
  learnPeople(info.people);
  state.channels = info.channels;
  state.maxUsers = info.maxUsers ?? 8;
  els.serverName.textContent = info.server;
  els.loginAddr.textContent = info.addresses[0] || location.origin;
  // список обновляется каждые 5 секунд — сохраняем то, что человек уже выбрал
  const chosen = els.loginChannel.value;
  els.loginChannel.innerHTML = info.channels
    .map((c) => `<option value="${c.id}">${c.emoji} ${escapeHtml(c.name)} (${c.count})</option>`)
    .join("");
  if (chosen && info.channels.some((c) => c.id === chosen)) {
    els.loginChannel.value = chosen;
  }
  els.loginPwWrap.hidden = !info.requiresPassword;
  renderSlots(info.online ?? 0, info.maxUsers ?? 8);
  return info;
}

/** Показывает занятые места и не даёт подключиться, когда всё занято. */
function renderSlots(online, max) {
  const free = Math.max(0, max - online);
  els.slotsLine.innerHTML = `Сейчас в сети: <b>${online} из ${max}</b>` +
    (free ? ` · свободно ${free}` : " · мест нет");
  els.slotsLine.classList.toggle("is-full", free === 0);

  els.slotsPill.textContent = `${online}/${max}`;
  els.slotsPill.classList.toggle("is-full", free === 0);
  els.slotsPill.title = free === 0 ? "Все места заняты" : `Свободно мест: ${free}`;

  if (els.app.hidden && free === 0 && els.loginBtn.textContent !== "Подключаем…") {
    els.loginBtn.disabled = true;
    els.loginBtn.classList.add("is-locked");
    els.loginBtn.textContent = "Все места заняты";
  } else if (free > 0 && els.loginBtn.classList.contains("is-locked")) {
    els.loginBtn.disabled = false;
    els.loginBtn.classList.remove("is-locked");
    els.loginBtn.textContent = "Подключиться";
  }
}

function connect() {
  const q = new URLSearchParams({
    sid: state.sid,
    uid: state.uid,
    name: state.name,
    channel: state.channel,
    pw: els.loginPass.value,
    // аватар не присылаем: сервер берёт его из базы по нашему номеру.
    // Раньше здесь уходила ссылка /avatars/имя.png, а сервер ждал имя файла
    // и молча отбрасывал её — картинка не доходила ни при входе, ни позже.
    circle: state.profile.showCircle ? "1" : "0",
  });
  const source = new EventSource(`/api/events?${q}`);
  state.source = source;

  source.onopen = () => {
    els.loginError.hidden = true;
  };
  source.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    handle(msg);
  };
  source.onerror = () => {
    if (state.source !== source) return;
    if (!els.app.hidden) toast("Связь с сервером потеряна — переподключаемся…", "warn");
  };
}

async function handle(msg) {
  switch (msg.e) {
    case "full":
      // сервер переполнен: закрываем поток, иначе EventSource будет долбиться снова
      state.source?.close();
      renderSlots(msg.online ?? msg.maxUsers, msg.maxUsers ?? 8);
      els.loginBtn.disabled = true;
      els.loginBtn.classList.add("is-locked");
      els.loginBtn.textContent = "Все места заняты";
      els.loginError.textContent =
        `На сервере уже ${msg.maxUsers} человек — это предел. ` +
        `Освободится место, когда кто-то выйдет.`;
      els.loginError.hidden = false;
      break;
    case "welcome":
      learnPeople(msg.people);
      state.me = msg.you;
      state.channel = msg.you.channel;
      state.users = new Map(msg.users.map((u) => [u.id, u]));
      state.channels = msg.channels;
      els.login.hidden = true;
      els.app.hidden = false;
      applyChannels();
      renderChat(msg.chat);
      renderUsers();
      updateControls();
      renderSlots(state.users.size, msg.server?.maxUsers ?? state.maxUsers);
      pingLoop();
      // спрашиваем микрофон сразу при входе: так соединения сразу с голосом
      ensureMic().then((ok) => {
        // В режиме «Всегда» микрофон обязан звучать сам. Раньше не звучал:
        // init() включает дорожку выключенной, и её включал только человек
        // — нажатием на кнопку или удержанием клавиши. В режиме «Всегда» тот,
        // кто вошёл, оставался нем, пока не нажмёт кнопку.
        if (ok && state.settings.mode !== "ptt" && voice.muted) {
          voice.setMic(true);
          send({ type: "update", muted: false, deafened: voice.deafened })
            .catch(() => {});
        }
        syncPeers();
        updateVoiceWarn();
        if (!ok) {
          toast("Микрофон не разрешён — нажмите «Микрофон», чтобы включить", "warn");
        }
      });
      break;
    case "users":
      learnPeople(msg.people);
      state.users = new Map(msg.users.map((u) => [u.id, u]));
      state.channels = msg.channels;
      // сначала обновляем себя — потом перерисовываем (порядок важен)
      const meNow = msg.users.find((u) => u.id === state.me.id);
      if (meNow) {
        state.me = meNow;
        state.channel = meNow.channel;
      }
      applyChannels();
      renderUsers();
      updateTiles();
      renderVideos();
      renderSlots(state.users.size, state.maxUsers);
      syncPeers();
      break;
    case "chat":
      addChatMessage(msg.message, true);
      break;
    case "signal":
      voice.handleSignal(msg.from, msg.data);
      break;
  }
}

/* ─────────────────── голос ─────────────────── */

const voice = new Voice({
  onState: ({ speaking } = {}) => {
    // событие «заговорил» приходит часто — перерисовываем только то, что зависит от него
    if (speaking !== undefined) {
      if (speaking) state.speaking.add(state.me.id);
      else state.speaking.delete(state.me.id);
      renderUsers();
      updateTiles();
      return;
    }
    updateControls();
  },
  onPeer: (peerId, info) => {
    if (!info) {
      state.speaking.delete(peerId);
      state.sharedVideo.delete(peerId);
      renderVideos();
      updateTiles();
      return;
    }
    if (info.video) {
      state.sharedVideo.set(peerId, info.video);
      renderVideos();
    }
    if (info.speaking !== undefined) {
      if (info.speaking) state.speaking.add(peerId);
      else state.speaking.delete(peerId);
      updateTiles();
    }
  },
  onError: (e) => toast(e.message || "Ошибка звука", "err"),
});
voice._send = send;
voice._volumes = new Map(Object.entries(JSON.parse(localStorage.getItem("ns.vol") || "{}")));
voice.setSelf(state.sid);
syncSettingsToVoice();

function syncPeers() {
  // пока браузер не ответил про микрофон — не строим соединения:
  // иначе в них не окажется звукового трека
  if (voice.micState === "pending") return;
  voice.setSelf(state.sid);
  const ids = [...state.users.values()]
    .filter((u) => u.channel === state.channel && u.id !== state.sid)
    .map((u) => u.id);
  voice.syncPeers(ids);
  for (const u of state.users.values()) voice.setRemoteMuted(u.id, u.muted);
}

let micGranted = false;

/**
 * Просит микрофон один раз и пересобирает соединения, если доступ
 * появился только сейчас (пользователь сначала вошёл без звука).
 */
async function ensureMic() {
  const ok = await voice.ensureMic();
  if (ok && !micGranted) {
    micGranted = true;
    voice.resetPeers();
    syncPeers();
  }
  return ok;
}

let pinging = false;
async function pingLoop() {
  // повторный вызов после переподключения не должен плодить интервалы
  if (pinging) return;
  pinging = true;
  const beat = async () => {
    const t = performance.now();
    const res = await api("/api/send", { sid: state.sid, type: "ping", t });
    const rtt = Math.round(performance.now() - t);
    if (res.ok !== false) {
      state.ping = rtt;
      els.pingPill.textContent = `${rtt} мс`;
      els.pingPill.classList.toggle("is-bad", rtt > 150);
      send({ type: "update", ping: rtt });
    }
  };
  beat();
  setInterval(beat, 5000);
}

/* ─────────────────── каналы ─────────────────── */

/** Запоминаем профили людей с сервера: по uid узнаём, чей это аватар. */
function learnPeople(list) {
  if (!Array.isArray(list)) return;
  for (const p of list) {
    if (!p || !p.uid) continue;
    state.people.set(p.uid, {
      name: p.name || "",
      avatarUrl: p.avatar || "",
      showCircle: p.showCircle !== false,
      color: p.color || "",
    });
  }
}

/** Профиль человека по uid — для чужих сообщений и аватаров. */
function profileOf(uid) {
  return (uid && state.people.get(uid)) || null;
}

function applyChannels() {
  const cur = state.me?.channel || state.channel;
  els.channelList.innerHTML = state.channels
    .map((c) => {
      const active = c.id === cur;
      // с картинкой: withMyProfile — мой аватар может быть свежее, чем в
      // списке участников, пришедшем с сервера
      const who = [...state.users.values()]
        .filter((u) => u.channel === c.id)
        .map((u) => memberView(withMyProfile(u)));
      const avatars = who.slice(0, 4).map((u) => u.avatar
        ? `<i class="is-pic" style="background:${u.color}" title="${escapeHtml(u.name)}">
             <img src="${escapeHtml(u.avatar)}" alt="" loading="lazy"></i>`
        : `<i style="background:${u.color}" title="${escapeHtml(u.name)}">${initials(u.name)}</i>`).join("");
      return `<button class="channel ${active ? "is-active" : ""}" data-id="${c.id}">
        <span class="channel__icon">${c.emoji}</span>
        <span class="channel__meta">
          <b>${escapeHtml(c.name)}</b>
          <small>${escapeHtml(c.about)}</small>
        </span>
        <span class="channel__avatars">${avatars}</span>
        ${who.length ? `<span class="channel__count">${who.length}</span>` : ""}
      </button>`;
    }).join("");

  els.channelList.querySelectorAll(".channel").forEach((btn) => {
    btn.onclick = () => switchChannel(btn.dataset.id);
  });

  const ch = state.channels.find((c) => c.id === cur);
  els.chatTitle.textContent = ch ? `${ch.emoji} ${ch.name}` : "Чат";
  els.chatText.placeholder = state.dm
    ? `Личное сообщение → ${state.dm.name}` : "Написать сообщение…";
  els.chatHint.textContent = state.dm ? "Личное сообщение · Esc — отменить" : "";
}

async function switchChannel(id) {
  if (id === state.channel) return closeDrawer();
  state.channel = id;
  state.dm = null;
  state.speaking.clear();
  if (state.me) state.me = { ...state.me, channel: id };
  els.chatLog.innerHTML = "";
  applyChannels();
  updateTiles();
  updateVoiceWarn();
  await send({ type: "update", channel: id });
  closeDrawer();
}

/* ─────────────────── участники ─────────────────── */

/** Перерисовывает то, где видно меня: плитки и список участников. */
function paintMyAvatar() {
  if (els.app.hidden) return;
  updateTiles();
  renderUsers();
}

function renderUsers() {
  const groups = new Map();
  for (const u of state.users.values()) {
    if (!groups.has(u.channel)) groups.set(u.channel, []);
    groups.get(u.channel).push(u);
  }
  let html = "";
  for (const c of state.channels) {
    const list = groups.get(c.id) || [];
    if (!list.length && c.id !== state.channel) continue;
    html += `<div class="group"><div class="group__title">${c.emoji} ${escapeHtml(c.name)}
      <span class="muted-xs">${list.length}</span></div>`;
    for (const u of list) {
      html += memberRow(u);
    }
    html += `</div>`;
  }
  els.membersList.innerHTML = html || `<p class="empty">Никого нет</p>`;
  els.membersCount.textContent = state.users.size;

  els.membersList.querySelectorAll("[data-vol]").forEach((slider) => {
    slider.oninput = () => {
      const v = Number(slider.value) / 100;
      voice.setVolume(slider.dataset.vol, v);
      const volMap = Object.fromEntries(voice._volumes);
      localStorage.setItem("ns.vol", JSON.stringify(volMap));
      slider.nextElementSibling.textContent = `${slider.value}%`;
    };
  });
  els.membersList.querySelectorAll("[data-dm]").forEach((el) => {
    el.onclick = () => {
      const u = state.users.get(el.dataset.dm);
      if (u) startDm(u);
    };
  });
}

function memberRow(u) {
  u = withMyProfile(u);
  const me = u.id === state.sid;
  const talking = state.speaking.has(u.id);
  const micIcon = u.muted ? "i-mic-off" : "i-mic";
  const vol = voice._volumes.get(u.id) ?? 1;
  const hideSlider = me;
  return `<div class="member ${talking ? "is-talking" : ""} ${me ? "is-me" : ""}"
               data-id="${u.id}">
    ${avatarMarkup(u)}
    <span class="member__name">${escapeHtml(u.name)}${me ? " <em>(вы)</em>" : ""}</span>
    <span class="member__tools">
      ${hideSlider ? "" : `<input type="range" class="vol" min="0" max="150" value="${Math.round(vol * 100)}" data-vol="${u.id}" title="Громкость"><b class="volval">${Math.round(vol * 100)}%</b>`}
      <span class="micstate ${u.muted ? "is-off" : ""}"><svg class="ic ic--sm"><use href="#${micIcon}"/></svg></span>
    </span>
    ${me ? "" : `<button class="member__dm" data-dm="${u.id}" title="Написать в личку">✉</button>`}
  </div>`;
}

/* Плитки «кто в канале»: прямоугольники 4:3, до трёх в строке.
   Число колонок подбирается так, чтобы сетка была ровной:
   колонок = min(3, ceil(√n)), заполнение по строкам, неполная строка
   по центру. Так 3 человека — это 2+1, а 7 — это 3+3+1. */
function updateTiles() {
  const people = [...state.users.values()].filter((u) => u.channel === state.channel);
  els.stageEmpty.hidden = people.length > 0;
  els.tileGrid.className = "stage__people " + sizeClass(people.length);
  // подсказываем интерфейсу, сколько места уступить чату
  els.app.classList.toggle("is-busy", people.length >= 5 && people.length < 8);
  els.app.classList.toggle("is-crowded", people.length >= 8);

  const tile = (u) => {
  const me = memberView(withMyProfile(u));
  const broken = linkBroken(u.id);
  return `
    <div class="tile ${state.speaking.has(u.id) ? "is-talking" : ""}"
         title="${escapeHtml(tileHint(u))}">
      ${avatarMarkup(me, "avatar avatar--lg")}
      ${broken ? `<span class="tile__noconn">${NO_SOUND_ICON}</span>` : ""}
      <span class="tile__name">${escapeHtml(me.name)}</span>
    </div>`;
};

  let html = "";
  for (let i = 0; i < people.length; i += columnsFor(people.length)) {
    html += `<div class="row">${people.slice(i, i + columnsFor(people.length)).map(tile).join("")}</div>`;
  }
  els.tileGrid.innerHTML = html;
}

const NO_SOUND_ICON =
  '<svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9H4z"/>'
  + '<path d="M17 9l4 6M21 9l-4 6"/></svg>';

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]
  ));
}

/**
 * Почему может не быть звука — коротко, в подсказке под плиткой.
 *
 * Раньше это было невозможно узнать: точка «говорит» молчит, а почему —
 * догадывайся. Теперь видно сразу: идёт соединение или нет, включён ли
 * микрофон у нас и не выключен ли звук.
 */
function tileHint(u) {
  if (u.id === state.sid) return u.name;
  const v = window.netspeak?.voice;
  const peer = v?.peers?.get(u.id);
  if (!peer) return `${u.name} — соединение ещё не началось`;
  const cs = peer.pc.connectionState;
  if (cs === "failed" || cs === "disconnected") {
    return `${u.name} — звук не идёт: связь оборвалась (${cs}). `
      + "Чаще всего мешает брандмауэр Windows или запрет обмена адресами в сети.";
  }
  if (cs !== "connected") return `${u.name} — соединяемся (${cs})`;
  if (!v.deafened && (v._volumes?.get(u.id) ?? 1) === 0) {
    return `${u.name} — громкость у вас стоит на нуле`;
  }
  if (v.deafened) return `${u.name} — звук у вас выключен`;
  if (peer.gainNode === null && !peer.audio) return `${u.name} — звук принять не вышло`;
  return `${u.name} — звук идёт`;
}

/** Обрыв связи с участником: значок показываем только тогда. */
function linkBroken(id) {
  if (!id || id === state.sid) return false;
  return voice.linkState(id).code === "не вышло";
}

/**
 * Полоса «звук не идёт».
 *
 * Показываем, когда с кем-то в канале связь не поднялась за отведённое
 * время. Раньше об этом не сообщалось вовсе: и значок на плитке, и
 * подсказка молчали, пока браузер честно стоял в состоянии «new» — то
 * есть выглядело, будто всё в порядке, а человек был нем.
 *
 * Заодно это и есть тот самый вопрос «звук вроде передаётся, но не
 * слышно»: подсказка прямо говорит, что данных не приходит вовсе.
 */
const WARN_HOWTO = `Подсказка под плиткой того, кого не слышно, скажет точно.

Чаще всего не пускает брандмауэр Windows: он не даёт двум компьютерам
обмениваться голосом напрямую. Откройте «Брандмауэр Windows → Правила для
входящих подключений» и убедитесь, что для NetSpeak.exe и
NetSpeakServer.exe есть разрешающие правила для частных сетей. Проще
всего запустить оба файла один раз и согласиться с запросом Windows
«Разрешить доступ».

Если в сети несколько адаптеров (VPN, виртуальные), отключите лишние на
время разговора.`;

let warnDismissed = false;

function updateVoiceWarn() {
  const box = els.voiceWarn;
  if (!box || els.app.hidden) return;
  if (warnDismissed) { box.hidden = true; return; }

  const mates = [...state.users.values()]
    .filter((u) => u.channel === state.channel && u.id !== state.sid);
  const bad = mates
    .map((u) => ({ u, s: voice.linkState(u.id) }))
    .filter((x) => x.s.code === "не вышло");

  if (!bad.length) { box.hidden = true; return; }
  const names = bad.map((x) => x.u.name).join(", ");
  els.voiceWarnTitle.textContent = bad.length === 1
    ? `Звук от «${names}» не идёт`
    : `Звук не идёт от: ${names}`;
  els.voiceWarnText.textContent = voice.micState !== "granted"
    ? "Микрофон не разрешён — нажмите «Микрофон», чтобы включить."
    : `Ждём связи уже ${bad[0].s.waited} с. Данных от собеседника не приходит.`;
  box.hidden = false;
}

/** Сколько плиток в строке: не больше трёх, и сетка должна быть ровной. */
function columnsFor(n) {
  if (n <= 0) return 1;
  return Math.min(MAX_PER_ROW, Math.ceil(Math.sqrt(n)));
}

/** Подбирает размер плиток под число участников. */
function sizeClass(n) {
  if (n <= 1) return "is-solo";
  if (n === 2) return "is-duo";
  if (n <= 4) return "is-small";
  if (n <= 6) return "is-medium";
  return "is-crowd";
}

function renderVideos() {
  const items = [...state.sharedVideo.entries()].filter(
    ([id]) => state.users.get(id)?.sharing);
  els.videos.innerHTML = items.map(([id, stream]) => {
    const u = state.users.get(id);
    const vid = `v_${id}`;
    setTimeout(() => {
      const el = document.getElementById(vid);
      if (el) { el.srcObject = stream; el.play().catch(() => {}); }
    }, 0);
    return `<div class="video"><video id="${vid}" autoplay playsinline muted></video>
      <span class="video__label">${escapeHtml(u?.name || "Экран")}</span></div>`;
  }).join("");
}

/* ─────────────────── чат ─────────────────── */

function startDm(u) {
  state.dm = u;
  resetAuthor();
  applyChannels();
  els.chatText.focus();
}

/** Автор и время последнего сообщения — чтобы не повторять имя подряд. */
let lastSender = null;
let lastAt = 0;
const NAME_REPEAT_AFTER = 5 * 60 * 1000; // через 5 минут имя показываем снова

function resetAuthor() {
  lastSender = null;
  lastAt = 0;
}

function renderChat(history) {
  els.chatLog.innerHTML = "";
  resetAuthor();
  const ch = state.me?.channel;
  history
    .filter((m) => !m.to && m.channel === ch)
    .forEach((m) => addChatMessage(m, false));
}

/** Время сообщения: «14:32:07». Одна функция, чтобы везде выглядело одинаково. */
function timeStr(ts) {
  return new Date((ts || 0) * 1000).toLocaleTimeString("ru-RU", {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function addChatMessage(m, scroll = true) {
  if (m.kind === "file") { addFile(m, scroll); return; }
  if (m.kind === "system") {
    // время слева от текста — так же, как у обычных сообщений
    els.chatLog.insertAdjacentHTML("beforeend",
      `<div class="sysmsg"><time>${timeStr(m.ts)}</time><span>${escapeHtml(m.text)}</span></div>`);
    scrollDown(scroll);
    return;
  }

  const mine = isMine(m);
  const priv = m.kind === "dm";
  const show = !priv || isMine(m) || m.to === state.sid;
  if (!show) return;
  // Сообщение показываем по тому, в каком канале оно написано. Раньше здесь
  // стояла ещё проверка «а автор сейчас в этом канале» — и она убирала всю
  // переписку от тех, кто уже вышел или перешёл в другой канал. С
  // сохранённой историей это было почти всегда: вернулись завтра — а от
  // вчерашнего разговора ни слова.
  if (!priv && m.channel !== state.channel) return;

  const time = timeStr(m.ts);
  // имя повторяем, если сменился автор или прошло больше 5 минут
  const ms = (m.ts || 0) * 1000;
  const showName = priv || m.from !== lastSender || ms - lastAt > NAME_REPEAT_AFTER;
  lastSender = m.from;
  lastAt = ms;
  const имя = showName
    ? `<span class="bubble__name" style="color:${m.color}">${escapeHtml(m.name)}</span>` : "";
  const body = priv
    ? `<div class="bubble bubble--dm"><span class="bubble__name">${mine ? "Вы → " + escapeHtml(m.name) : escapeHtml(m.name) + " → Вы"}</span>
       <p>${linkify(escapeHtml(m.text))}</p></div>`
    : `<div class="bubble">${имя}<p>${linkify(escapeHtml(m.text))}</p></div>`;

  els.chatLog.insertAdjacentHTML("beforeend",
    `<div class="msg ${mine ? "msg--mine" : ""}">
       ${avatarFor(m)}
       <div class="msg__body">${body}</div>
       <time>${time}</time>
     </div>`);
  scrollDown(scroll);
}

function addFile(m, scroll) {
  const mine = isMine(m);
  lastSender = m.from;
  lastAt = (m.ts || 0) * 1000;
  // мелкие файлы в байтах, иначе получается нелепое «0.0 КБ»
  let size, unit;
  if (m.size < 1024) { size = m.size; unit = "Б"; }
  else if (m.size < 1048576) { size = (m.size / 1024).toFixed(1); unit = "КБ"; }
  else { size = (m.size / 1048576).toFixed(1); unit = "МБ"; }
  els.chatLog.insertAdjacentHTML("beforeend", `
    <div class="msg ${mine ? "msg--mine" : ""}">
      ${avatarFor(m)}
      <div class="msg__body">
        <span class="bubble__name" style="color:${m.color}">${escapeHtml(m.name)}</span>
        <a class="file" href="/files/${m.fileId}/${encodeURIComponent(m.filename)}" download>
          <span class="file__ico"><svg class="ic"><use href="#i-down"/></svg></span>
          <span class="file__meta"><b>${escapeHtml(m.filename)}</b><small>${size} ${unit}</small></span>
        </a>
      </div>
      <time>${timeStr(m.ts)}</time>
    </div>`);
  scrollDown(scroll);
}

function scrollDown(force) {
  if (!force) return;
  const log = els.chatLog;
  const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 160;
  if (nearBottom) log.scrollTop = log.scrollHeight;
}

/* ─────────────────── управление ─────────────────── */

function updateControls() {
  // !! обязателен: track до запроса микрофона равен null, и тогда on === undefined.
  // classList.toggle("x", undefined) по спецификации WebIDL трактует undefined как
  // «аргумент не передан» и ПЕРЕКЛЮЧАЕТ класс вместо того, чтобы его снять.
  // Микрофон считаем включённым, только если он получен И не заглушён:
  // voice.track — сырой вход, он включён всегда.
  const on = voice.micLive();
  els.micBtn.classList.toggle("is-on", on);
  els.micBtn.classList.toggle("is-off", !on);
  els.micBtn.querySelector("use").setAttribute("href", on ? "#i-mic" : "#i-mic-off");
  els.micLabel.textContent = on ? "Микрофон вкл" : "Микрофон выкл";

  els.deafenBtn.classList.toggle("is-on", !voice.deafened);
  els.deafenBtn.classList.toggle("is-off", !!voice.deafened);
  els.deafenBtn.querySelector("use").setAttribute("href", voice.deafened ? "#i-head" : "#i-head");
  els.deafenBtn.querySelector("span").textContent = voice.deafened ? "Звук выкл" : "Звук вкл";

  els.pttHint.hidden = state.settings.mode !== "ptt";
  if (state.settings.mode === "ptt") {
    els.pttHint.textContent = `Говорите: удерживайте ${keyLabel(state.settings.key)}`;
  }

  renderUsers();
  updateTiles();
  if (!els.settingsModal.hidden) refreshHotkeyCap();
}

async function toggleMic() {
  try {
    if (!(await ensureMic())) throw voice.lastMicError || new Error("доступ запрещён");
    if (state.settings.mode === "ptt") {
      toast("Сейчас включён режим «Удержанием» — говорите клавишей", "warn");
      return;
    }
    voice.setMic(voice.muted);
    send({ type: "update", muted: voice.muted, deafened: voice.deafened });
  } catch (e) {
    toast("Не удалось включить микрофон: " + (e.message || e), "err");
  }
}

async function toggleDeafen() {
  await ensureMic();
  voice.setDeaf(!voice.deafened);
  send({ type: "update", muted: voice.muted, deafened: voice.deafened });
}

/* Показ экрана отключён: кнопки в интерфейсе нет.
   Функция оставлена на случай возврата — достаточно вернуть кнопку. */
async function toggleShare() {
  try {
    await ensureMic();
    if (voice.isSharing) {
      await voice.stopScreen();
      send({ type: "update", sharing: false });
    } else {
      await voice.startScreen();
      send({ type: "update", sharing: true });
    }
    updateControls();
  } catch (e) {
    toast("Не удалось показать экран", "err");
  }
}

/* ─────────────────── события ─────────────────── */

els.loginForm.onsubmit = async (e) => {
  e.preventDefault();
  // профиль мог ещё грузиться — иначе подключимся без аватара
  await profileReady;
  state.name = els.loginName.value.trim() || "Гость";
  // Имя уходит в файл профиля, а не только в память браузера: иначе при
  // перезапуске программы его пришлось бы вводить заново.
  await saveProfile({ name: state.name });
  // выбор канала читаем ДО loadInfo: тот пересобирает список и сбрасывает его
  const wanted = els.loginChannel.value;
  els.loginError.hidden = true;
  els.loginBtn.disabled = true;
  els.loginBtn.classList.remove("is-locked");
  els.loginBtn.textContent = "Подключаем…";
  const info = await loadInfo();
  const exists = info.channels.some((c) => c.id === wanted);
  state.channel = exists ? wanted : (els.loginChannel.value || info.channels[0].id);
  connect();
  setTimeout(() => {
    if (!els.app.hidden) return; // подключились — всё хорошо
    els.loginBtn.disabled = false;
    els.loginBtn.textContent = "Подключиться";
    els.loginError.textContent = "Не удалось подключиться. Проверьте адрес и что сервер запущен.";
    els.loginError.hidden = false;
  }, 6000);
};

els.chatForm.onsubmit = async (e) => {
  e.preventDefault();
  const text = els.chatText.value.trim();
  if (!text) return;
  els.chatText.value = "";
  const res = await send({ type: "chat", text, to: state.dm?.id || null });
  if (res.ok === false && res.error) toast(res.error, "warn");
};

els.chatText.onkeydown = (e) => {
  if (e.key === "Escape" && state.dm) {
    state.dm = null;
    applyChannels();
  }
};

els.micBtn.onclick = toggleMic;
els.deafenBtn.onclick = toggleDeafen;

els.leaveBtn.onclick = async () => {
  // после выхода показываем окно входа, а не заходим автоматически
  sessionStorage.setItem("ns.welcomed", "1");
  await send({ type: "leave" }).catch(() => {});
  state.source?.close();
  voice.close();
  location.reload();
};

els.btnChannels.onclick = () => {
  els.sidebar.classList.add("is-open");
  els.scrim.hidden = false;
};
els.closeSidebar.onclick = closeDrawer;
els.scrim.onclick = closeDrawer;
function closeDrawer() {
  els.sidebar.classList.remove("is-open");
  els.scrim.hidden = true;
}

/* ── какая клавиша-модификатор нажата ──
   У события нажатия САМОЙ Ins поле location всегда 0, поэтому по нему нельзя
   отличить левый Ctrl от правого: «Правый Ctrl + Ins» срабатывал бы и на левом.
   Сторону приходится запоминать отдельно, по keydown/keyup самих Ctrl/Shift/Alt.
   Регистрируем раньше остальных обработчиков — они читают эти значения. */
const modSide = { Control: 0, Shift: 0, Alt: 0 };
const MOD_RE = /^(Control|Shift|Alt)(Left|Right)$/;
document.addEventListener(
  "keydown",
  (e) => {
    const m = MOD_RE.exec(e.code || "");
    if (m) modSide[m[1]] = m[2] === "Right" ? 2 : 1;
  },
  true
);
document.addEventListener(
  "keyup",
  (e) => {
    const m = MOD_RE.exec(e.code || "");
    if (m) modSide[m[1]] = 0;
  },
  true
);
for (const ev of ["blur", "visibilitychange"]) {
  window.addEventListener(ev, () => {
    modSide.Control = modSide.Shift = modSide.Alt = 0;
  });
}

/* push-to-talk: работает и с клавишей, и с боковой кнопкой мыши */
const isPttDown = (e) =>
  e.code === state.settings.key ||
  (e.type === "mousedown" &&
    state.settings.key === "Mouse" + e.button &&
    (e.button === 3 || e.button === 4));

document.addEventListener("keydown", async (e) => {
  if (capturing || state.settings.mode !== "ptt") return;
  if (isTyping(e.target)) return;
  // ориентируемся на voice.muted: voice.track — это сырой вход микрофона,
  // он включён всегда, а включается и выключается исходящая дорожка outTrack
  if (isPttDown(e) && voice.muted) {
    e.preventDefault();
    if (!(await ensureMic())) {
      toast(MIC_SUPPORTED ? "Нужен доступ к микрофону" : "Микрофон доступен только по HTTPS", "warn");
      return;
    }
    voice.setMic(true);
    send({ type: "update", muted: false });
  }
});
document.addEventListener("keyup", async (e) => {
  if (capturing || state.settings.mode !== "ptt") return;
  if (isPttDown(e) && !voice.muted) {
    e.preventDefault();
    voice.setMic(false);
    send({ type: "update", muted: true });
  }
});
document.addEventListener("mousedown", async (e) => {
  if (capturing || state.settings.mode !== "ptt") return;
  if (e.target.closest("button, input, select, textarea, a")) return;
  if (isPttDown(e) && voice.muted) {
    e.preventDefault();
    await ensureMic();
    voice.setMic(true);
    send({ type: "update", muted: false });
  }
});
document.addEventListener("mouseup", (e) => {
  if (capturing || state.settings.mode !== "ptt") return;
  if (isPttDown(e) && !voice.muted) {
    voice.setMic(false);
    send({ type: "update", muted: true });
  }
});
const isTyping = (el) => el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT");

/* ── горячая клавиша: включить/выключить микрофон откуда угодно ── */
document.addEventListener("keydown", async (e) => {
  if (capturing || !hotkeyUsable()) return;
  if (!comboMatches(e, state.settings.hotkey)) return;
  e.preventDefault();
  if (isTyping(e.target)) return; // не мешаем печатать в чате
  await toggleMic();
});

/* файлы */
els.attachBtn.onclick = () => els.fileInput.click();
els.fileInput.onchange = async () => {
  const file = els.fileInput.files[0];
  els.fileInput.value = "";
  if (!file) return;
  if (file.size > 32 * 1024 * 1024) return toast("Файл больше 32 МБ", "warn");
  toast(`Отправляю «${file.name}»…`);
  const buf = await file.arrayBuffer();
  let bin = "";
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  const res = await api("/api/send", {
    sid: state.sid, type: "upload", filename: file.name, data: btoa(bin),
  });
  if (!res.ok) toast(res.error || "Не удалось отправить файл", "err");
};

/* ─────────────────── настройки звука ─────────────────── */

els.btnSettings.onclick = openSettings;
els.closeSettings.onclick = () => (els.settingsModal.hidden = true);

/* ── профиль: имя, картинка, кружок ── */

function myColor() {
  return `var(--c, ${state.me?.color || "#3fc79a"})`;
}

/** Кружок в настройках: картинка, если есть; иначе буква, если кружок включён. */
function refreshProfileUi() {
  const p = state.profile;
  if (els.profileName.value !== (state.name || "")) els.profileName.value = state.name || "";
  els.showCircle.checked = p.showCircle !== false;

  const wantCircle = p.showCircle !== false;
  els.avatarCircle.hidden = !!p.avatarUrl || !wantCircle;
  if (p.avatarUrl) {
    els.avatarImg.src = p.avatarUrl;
    els.avatarImg.hidden = false;
    els.avatarClearBtn.hidden = false;
  } else {
    els.avatarImg.hidden = true;
    els.avatarImg.removeAttribute("src");
    els.avatarClearBtn.hidden = true;
  }
  const letter = (state.name || "?").trim()[0] || "?";
  els.avatarCircle.textContent = letter.toUpperCase();
  els.avatarCircle.style.setProperty("--c", state.me?.color || "var(--accent)");
}

els.avatarPick.onclick = els.avatarPickBtn.onclick = () => els.avatarInput.click();

els.avatarInput.onchange = async () => {
  const file = els.avatarInput.files[0];
  els.avatarInput.value = "";
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) {
    toast("Картинка больше 2 МБ — возьмите поменьше", "warn");
    return;
  }
  const dataUrl = await new Promise((res) => {
    const fr = new FileReader();
    fr.onload = () => res(String(fr.result));
    fr.onerror = () => res("");
    fr.readAsDataURL(file);
  });
  if (!dataUrl) return toast("Не удалось прочитать картинку", "err");

  if (storeApi) {
    try {
      const r = await fetch("/api/avatar?uid=" + encodeURIComponent(state.uid), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image: dataUrl, uid: state.uid }),
      });
      const saved = await r.json();
      if (saved && saved.avatarUrl) {
        // сервер вернул ссылку с номером версии: она меняется при каждой
        // новой картинке, и браузер обновляет аватар сам
        // сервер дал новое имя файла — старое уже удалено у него
        state.profile.avatar = saved.avatar || "";
        state.profile.avatarUrl = saved.avatarUrl;
      } else {
        state.profile.avatarUrl = dataUrl;
      }
    } catch {
      state.profile.avatarUrl = dataUrl; // сервера нет — держим в памяти вкладки
    }
  } else {
    state.profile.avatarUrl = dataUrl;
  }
  refreshProfileUi();
  paintMyAvatar();
  pushProfileToServer();
};

els.avatarClearBtn.onclick = async () => {
  if (storeApi) {
    try {
      await fetch("/api/avatar?uid=" + encodeURIComponent(state.uid), { method: "DELETE" });
    } catch { /* не страшно */ }
  }
  state.profile.avatar = "";
  state.profile.avatarUrl = "";
  refreshProfileUi();
  paintMyAvatar();
  pushProfileToServer();
};

els.profileName.onchange = async () => {
  const clean = els.profileName.value.trim().slice(0, 24) || "Гость";
  els.profileName.value = clean;
  state.name = clean;
  await saveProfile({ name: clean });
  // имя изменилось — сообщаем комнате, иначе у собеседников останется старое
  if (state.me && !els.app.hidden) send({ type: "update", name: clean }).catch(() => {});
  refreshProfileUi();
  paintMyAvatar();
};

els.showCircle.onchange = () => {
  saveProfile({ showCircle: els.showCircle.checked });
  refreshProfileUi();
  paintMyAvatar();
  pushProfileToServer();
};

/** Картинка участника, если она есть; иначе кружок с буквой.
 *  Если картинки нет и кружок выключен — остаётся пустое место. */
function avatarMarkup(u, cls = "avatar", extra = "") {
  if (u.avatar) {
    // --pic добавляется отдельным классом: cls может быть составным
    // («avatar avatar--lg»), и приписывание к нему дало бы чушь
    return `<span class="${cls} avatar--pic" style="--c:${u.color}" ${extra}>` +
      `<img src="${escapeHtml(u.avatar)}" alt="" loading="lazy"></span>`;
  }
  const letter = u.showCircle === false ? "" : escapeHtml(initials(u.name));
  return `<span class="${cls}" style="--c:${u.color}" ${extra}>${letter}</span>`;
}

/** Аватар автора сообщения.
 *
 *  Свой — из своего профиля: он свежее, чем то, что прислал сервер.
 *  Чужой — сперва из базы сервера по uid, потому что автор мог уже выйти
 *  из сети, а из списка участников — если он в сети прямо сейчас.
 */
function avatarFor(m, cls = "avatar avatar--sm") {
  if (isMine(m)) {
    return avatarMarkup({
      name: m.name,
      color: m.color,
      avatar: state.profile.avatarUrl || state.me?.avatar || "",
      showCircle: state.profile.showCircle,
    }, cls);
  }
  const known = state.users.get(m.from);
  if (known) return avatarMarkup(known, cls);
  const fromDb = profileOf(m.uid);
  if (fromDb) {
    return avatarMarkup({
      name: fromDb.name || m.name,
      color: fromDb.color || m.color,
      avatar: fromDb.avatarUrl || "",
      showCircle: fromDb.showCircle,
    }, cls);
  }
  return avatarMarkup({ name: m.name, color: m.color, avatar: "" }, cls);
}

/* ─────────────────── проверка звука ─────────────────── */

/**
 * Проверка по одному нажатию.
 *
 * «Звука нет» — плохой вопрос: причин много, и выглядят они одинаково.
 * Здесь три шага, и каждый отвечает сам за себя:
 *
 *   1. колонки — короткий тон; если его слышно, слышимо всё остальное;
 *   2. микрофон — меряем две секунды; если тишина, дело не в сети;
 *   3. связь с каждым в канале — соединено ли и идут ли байты.
 *
 * Ответы по-русски и без терминов: сразу видно, где обрыв.
 */
function checkLine(kind, text, note) {
  return `<div class="vcheck__line ${kind}"><span class="vcheck__mark">${
    kind === "ok" ? "✓" : kind === "no" ? "✕" : "…"
  }</span><span>${escapeHtml(text)}${
    note ? `<br><span class="vcheck__note">${escapeHtml(note)}</span>` : ""
  }</span></div>`;
}

/** Короткий тон в колонки: проверяем, умеем ли вообще что-то играть. */
async function testSpeakers() {
  const ctx = voice._ctx();
  if (ctx.state !== "running") await ctx.resume().catch(() => {});
  const osc = ctx.createOscillator();
  const amp = ctx.createGain();
  osc.type = "sine";
  osc.frequency.value = 660;
  // мягкий вход и выход: щелчок на начале и конце слышен неприятно
  amp.gain.setValueAtTime(0, ctx.currentTime);
  amp.gain.linearRampToValueAtTime(0.25, ctx.currentTime + 0.04);
  amp.gain.setValueAtTime(0.25, ctx.currentTime + 0.5);
  amp.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.6);
  osc.connect(amp);
  amp.connect(ctx.destination);
  osc.start();
  osc.stop(ctx.currentTime + 0.65);
  await new Promise((r) => setTimeout(r, 750));
}

/** Две секунды слушаем микрофон: он вообще что-то слышит? */
async function testMic(ms = 2000) {
  if (!(await ensureMic())) {
    return { ok: false, why: voice.lastMicError?.message || "доступ не разрешён" };
  }
  const analyser = voice.chain?.analyser;
  if (!analyser) return { ok: false, why: "микрофон получен, но обработка не построилась" };
  const buf = new Uint8Array(analyser.frequencyBinCount);
  const outBuf = new Uint8Array((voice.chain?.out || analyser).frequencyBinCount);
  let peak = 0;
  let outPeak = 0;
  const end = Date.now() + ms;
  while (Date.now() < end) {
    analyser.getByteFrequencyData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    peak = Math.max(peak, Math.sqrt(sum / buf.length));
    // отдельно меряем то, что реально уходит в сеть: разница между входом
    // и выходом — это ровно то, что человек не может увидеть иначе
    if (voice.chain?.out) {
      voice.chain.out.getByteFrequencyData(outBuf);
      let s2 = 0;
      for (let i = 0; i < outBuf.length; i++) s2 += outBuf[i] * outBuf[i];
      outPeak = Math.max(outPeak, Math.sqrt(s2 / outBuf.length));
    }
    await new Promise((r) => setTimeout(r, 60));
  }
  return { ok: peak > 6, peak: Math.round(peak), out: Math.round(outPeak) };
}

async function runSoundCheck() {
  const out = els.soundCheckOut;
  const btn = els.btnSoundCheck;
  btn.disabled = true;
  out.hidden = false;
  out.innerHTML = checkLine("wait", "Проверяем…");

  const lines = [];
  try {
    lines.push(checkLine("wait", "Колонки: сейчас будет короткий тон"));
    await testSpeakers();
    lines[0] = checkLine("ok", "Тон отправлен в колонки",
      "Если вы его не слышали — дело в колонках или в системной громкости, а не в сети");
  } catch (e) {
    lines[0] = checkLine("no", "Колонки: звук отправить не вышло", String(e.message || e));
  }

  const mic = await testMic();
  if (mic.why) {
    lines.push(checkLine("no", "Микрофон недоступен", mic.why));
  } else if (!mic.ok) {
    lines.push(checkLine("no", "Микрофон молчит",
      "Проверка шла 2 секунды — скажите что-нибудь вслух. Если и так тишина, "
      + "микрофон выбран не тот или выключен в Windows."));
  } else if (!voice.outTrack || !voice.outTrack.enabled) {
    lines.push(checkLine("no", "Микрофон слышит, но в сеть уходит тишина",
      "Дорожка выключена — нажмите «Микрофон»."));
  } else if (mic.out < 2) {
    lines.push(checkLine("no", "В сеть уходит тишина, хотя микрофон слышит",
      "Порог шума приглушает всё. Поставьте его в настройках на 0 "
      + "(«Порог шума») и проверьте снова."));
  } else {
    lines.push(checkLine("ok", "Микрофон слышит и в сеть идёт звук",
      `вход ${mic.peak}, выход ${mic.out}`));
  }

  const ctxState = voice._ctx().state;
  if (ctxState !== "running") {
    lines.push(checkLine("no", `Звуковой движок приостановлен (${ctxState})`,
      "Щёлкните мышью в окне — он запускается по действию пользователя."));
  }

  const mates = [...state.users.values()]
    .filter((u) => u.channel === state.channel && u.id !== state.sid);
  if (!mates.length) {
    lines.push(checkLine("wait", "Связь: никто больше не в этом канале"));
  } else {
    for (const u of mates) {
      const st = voice.linkState(u.id);
      const rep = await voice.linkReport(u.id).catch(() => null);
      if (st.code === "ок") {
        lines.push(checkLine("ok", `Связь с «${u.name}» есть`,
          rep && rep.bytes ? `пришло ${Math.round(rep.bytes / 1024)} КБ звука`
                           : "звук ещё не передавался — пусть что-нибудь скажет"));
      } else if (st.code === "не вышло") {
        lines.push(checkLine("no", `Связь с «${u.name}» не устанавливается`,
          `ждали ${st.waited} с. Чаще всего не пускает брандмауэр — см. «Разрешить в брандмауэре» в окне сервера`));
      } else {
        lines.push(checkLine("wait", `Связь с «${u.name}»: соединяемся`,
          `ждём ${st.waited} с`));
      }
      if (u.muted) {
        lines.push(checkLine("no", `«${u.name}» выключил микрофон`,
          "у него полоса «Микрофон выкл»"));
      }
    }
  }

  out.innerHTML = lines.join("");
  btn.disabled = false;
}

els.btnSoundCheck?.addEventListener("click", () => { runSoundCheck().catch(() => {}); });

els.saveSettings.onclick = async () => {
  // запоминаем, что было у браузера: галочки требуют нового запроса микрофона
  const before = {
    echo: voice.settings.echo,
    noise: voice.settings.noise,
    autoGain: voice.settings.autoGain,
    inputDevice: voice.settings.inputDevice,
  };

  state.settings = {
    mode: els.modeSegment.querySelector(".is-active").dataset.mode,
    key: state.settings.key,
    hotkey: state.settings.hotkey,
    mic: els.micDevice.value,
    speaker: els.speakerDevice.value,
    gain: Number(els.micGain.value),
    highpass: Number(els.highpass.value),
    gate: Number(els.gate.value),
    leveling: Number(els.leveling.value),
    echo: els.optEcho.checked,
    noise: els.optNoise.checked,
    agc: els.optAgc.checked,
  };
  localStorage.setItem("ns.settings", JSON.stringify(state.settings));
  saveAppSettings(); // и на диск, в settings/app.json

  voice.settings.echo = state.settings.echo;
  voice.settings.noise = state.settings.noise;
  voice.settings.autoGain = state.settings.agc;
  voice.settings.inputDevice = state.settings.mic;
  voice.settings.outputDevice = state.settings.speaker;
  voice._applyAudioSettings();

  els.settingsModal.hidden = true;
  updateControls();

  const changed = ["echo", "noise", "autoGain"].some((k) => before[k] !== voice.settings[k]) ||
    before.inputDevice !== voice.settings.inputDevice;
  if (voice.track && changed) {
    try {
      await voice.reconfigure();
      toast("Настройки применены");
    } catch (e) {
      toast("Не удалось применить: " + (e.message || e), "err");
    }
  } else {
    toast("Настройки сохранены");
  }
  renderMicStatus();
};

els.modeSegment.onclick = (e) => {
  const btn = e.target.closest(".seg");
  if (!btn) return;
  els.modeSegment.querySelectorAll(".seg").forEach((s) => s.classList.remove("is-active"));
  btn.classList.add("is-active");
  // режим применяем сразу: от него зависит доступность горячей клавиши
  state.settings.mode = btn.dataset.mode;
  // и сразу сохраняем: иначе выбор «Всегда» терялся бы при перезагрузке
  localStorage.setItem("ns.settings", JSON.stringify(state.settings));
  saveAppSettings();
  els.keyField.hidden = btn.dataset.mode !== "ptt";
  refreshHotkeyCap();
  updateControls();
};

/* ── выбор клавиш: и раскладки, и горячей клавиши ── */
let capturing = null; // "ptt" | "hotkey" | null

function startCapture(which) {
  capturing = which;
  const btn = which === "ptt" ? els.pttKey : els.micHotkey;
  btn.classList.add("is-capturing");
  btn.querySelector(".keycap__val").textContent = "Нажмите…";
  btn.querySelector(".keycap__hint").textContent = "Esc — отмена";
}

function stopCapture() {
  if (!capturing) return;
  const which = capturing;
  capturing = null;
  const btn = which === "ptt" ? els.pttKey : els.micHotkey;
  btn.classList.remove("is-capturing");
  btn.querySelector(".keycap__hint").textContent =
    which === "ptt" ? "нажмите, чтобы изменить" : "нажмите сочетание";
  refreshKeycap();
  refreshHotkeyCap();
}

document.addEventListener("keydown", (e) => {
  if (!capturing) return;
  e.preventDefault();
  e.stopPropagation();
  if (e.key === "Escape") return stopCapture();
  if (capturing === "ptt") {
    if (["Shift", "Control", "Alt", "Meta"].includes(e.key) && e.code === e.key) return;
    const code = e.code || codeFromKey(e.key);
    if (!code) return;
    state.settings.key = code;
  } else {
    // модификатор жмём первым, но записываем только когда придёт основная клавиша
    if (MOD_RE.test(e.code || "") || e.key === "Meta") return;
    const combo = comboFromEvent(e);
    if (!combo) return;
    state.settings.hotkey = combo;
  }
  localStorage.setItem("ns.settings", JSON.stringify(state.settings));
  stopCapture();
  updateControls();
});

els.pttKey.onclick = () => startCapture("ptt");
els.micHotkey.onclick = () => startCapture("hotkey");

function codeFromKey(key) {
  if (key === " " || key === "Spacebar") return "Space";
  if (key.length === 1) {
    const c = key.toUpperCase();
    if (/[A-Z]/.test(c)) return "Key" + c;
    if (/[0-9]/.test(c)) return "Digit" + c;
  }
  return "";
}

// боковые кнопки мыши — их нет среди KeyboardEvent.code
document.addEventListener("mousedown", (e) => {
  if (capturing !== "hotkey") return;
  if (e.button === 3 || e.button === 4) {
    e.preventDefault();
    state.settings.hotkey = "Mouse" + e.button;
    localStorage.setItem("ns.settings", JSON.stringify(state.settings));
    stopCapture();
    updateControls();
  }
});

els.micGain.oninput = () => {
  els.gainValue.textContent = els.micGain.value + "%";
  voice.settings.gain = Number(els.micGain.value);
  voice._applyAudioSettings();
  paintRange(els.micGain);
};

// ползунки обработки звука: слышно изменения сразу, без нажатия «Готово»
els.highpass.oninput = () => {
  const v = Number(els.highpass.value);
  els.highpassValue.textContent = v ? v + " Гц" : "выключено";
  voice.settings.highpass = v;
  voice._applyAudioSettings();
  paintRange(els.highpass);
};
els.gate.oninput = () => {
  const v = Number(els.gate.value);
  els.gateValue.textContent = v ? String(v) : "выключено";
  voice.settings.gate = v;
  state.settings.gate = v;   // шкала сразу покажет новый порог
  paintRange(els.gate);
};
els.leveling.oninput = () => {
  const v = Number(els.leveling.value);
  els.levelingValue.textContent = v ? String(v) : "выключено";
  voice.settings.leveling = v;
  voice._applyAudioSettings();
  paintRange(els.leveling);
};

async function openSettings() {
  els.settingsModal.hidden = false;
  refreshProfileUi(); // сначала подтягиваем актуальный профиль
  els.modeSegment.querySelectorAll(".seg").forEach((s) =>
    s.classList.toggle("is-active", s.dataset.mode === state.settings.mode));
  els.keyField.hidden = state.settings.mode !== "ptt";
  refreshKeycap();
  refreshHotkeyCap();
  els.micGain.value = state.settings.gain;
  els.gainValue.textContent = state.settings.gain + "%";
  els.highpass.value = state.settings.highpass;
  els.highpassValue.textContent = state.settings.highpass ? state.settings.highpass + " Гц" : "выключено";
  els.gate.value = state.settings.gate;
  els.gateValue.textContent = state.settings.gate ? String(state.settings.gate) : "выключено";
  els.leveling.value = state.settings.leveling;
  els.levelingValue.textContent = state.settings.leveling ? String(state.settings.leveling) : "выключено";
  for (const el of [els.micGain, els.highpass, els.gate, els.leveling]) paintRange(el);
  els.optEcho.checked = state.settings.echo;
  els.optNoise.checked = state.settings.noise;
  els.optAgc.checked = state.settings.agc;
  // ползунки применим сразу, до нажатия «Готово»
  voice.settings.gain = state.settings.gain;
  voice.settings.highpass = state.settings.highpass;
  voice.settings.gate = state.settings.gate;
  voice.settings.leveling = state.settings.leveling;
  voice._applyAudioSettings();

  if (await ensureMic()) {
    const devices = await navigator.mediaDevices.enumerateDevices();
    fillSelect(els.micDevice, devices.filter((d) => d.kind === "audioinput"), state.settings.mic, "Микрофон по умолчанию");
    fillSelect(els.speakerDevice, devices.filter((d) => d.kind === "audiooutput"), state.settings.speaker, "Динамики по умолчанию");
  }
  renderMicStatus();
  startMeter();
}

function fillSelect(sel, list, current, placeholder) {
  sel.innerHTML = `<option value="">${placeholder}</option>` +
    list.map((d, i) => `<option value="${d.deviceId}">${escapeHtml(d.label || `Устройство ${i + 1}`)}</option>`).join("");
  sel.value = current || "";
}

const METER_SCALE = 70; // верхняя граница шкалы микрофона
const MAX_PER_ROW = 3;  // максимум плиток в строке

function startMeter() {
  cancelAnimationFrame(voice._meterRaf);
  const cut = els.micMeterCut;
  const loud = els.micMeterLoud;
  const gateMark = els.micMeterGate;
  const pct = (v) => Math.max(0, Math.min(100, (v / METER_SCALE) * 100));

  const loop = () => {
    if (els.settingsModal.hidden) return;
    const level = Math.min(METER_SCALE, voice.level || 0);
    const th = Math.min(METER_SCALE, Number(state.settings.gate) || 0);

    if (th > 0) {
      // слева от порога всё срежется — рисуем красным
      cut.style.display = "block";
      gateMark.style.display = "block";
      cut.style.width = pct(Math.min(level, th)) + "%";
      gateMark.style.left = pct(th) + "%";
      loud.style.left = pct(th) + "%";
      loud.style.width = pct(Math.max(0, level - th)) + "%";
    } else {
      cut.style.display = "none";
      gateMark.style.display = "none";
      loud.style.left = "0";
      loud.style.width = pct(level) + "%";
    }
    voice._meterRaf = requestAnimationFrame(loop);
  };
  loop();
}

/** Показывает заполненную часть ползунка. */
function paintRange(el) {
  const box = el.closest(".range");
  if (!box) return;
  const min = Number(el.min), max = Number(el.max);
  const p = ((Number(el.value) - min) / (max - min)) * 100;
  box.style.setProperty("--fill", p.toFixed(1) + "%");
}

/* ─────────────────── тема и микрофон ─────────────────── */

/**
 * Микрофон браузеры открывают только в защищённом контексте. По обычному HTTP
 * из локальной сети navigator.mediaDevices вообще не существует — поэтому
 * предупреждаем сразу, а не молча ловим ошибку.
 */
const MIC_SUPPORTED = !!navigator.mediaDevices?.getUserMedia;
if (!MIC_SUPPORTED) els.micWarn.hidden = false;

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const icon = theme === "light" ? "#i-moon" : "#i-sun";
  const title = theme === "light" ? "Включить тёмную тему" : "Включить светлую тему";
  // солнышки два: в приложении и на экране входа
  for (const [btn, use] of [[els.btnTheme, els.themeIcon], [els.btnThemeLogin, els.themeIconLogin]]) {
    if (!btn || !use) continue;
    use.setAttribute("href", icon);
    btn.classList.toggle("is-light", theme === "light");
    btn.title = title;
  }
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  localStorage.setItem("ns.theme", next);
  applyTheme(next);
}

els.btnTheme.onclick = toggleTheme;
els.btnThemeLogin.onclick = toggleTheme;
els.btnSettingsLogin.onclick = openSettings;

function keyLabel(code) {
  const map = {
    Space: "Пробел", ShiftLeft: "Левый Shift", ShiftRight: "Правый Shift",
    ControlLeft: "Левый Ctrl", ControlRight: "Правый Ctrl",
    AltLeft: "Левый Alt", AltRight: "Правый Alt",
    Tab: "Tab", CapsLock: "Caps Lock", Backquote: "Ё", Backslash: "\\",
    Enter: "Enter", Backspace: "Backspace", Delete: "Delete",
    Insert: "Ins", Home: "Home", End: "End",
    PageUp: "PgUp", PageDown: "PgDn", ContextMenu: "Кнопка меню",
    Mouse4: "Боковая кнопка 1", Mouse5: "Боковая кнопка 2",
  };
  if (map[code]) return map[code];
  if (code.startsWith("Key")) return code.slice(3);
  if (code.startsWith("Digit")) return code.slice(5);
  if (code.startsWith("Numpad")) return "Цифра " + code.slice(6);
  if (code.startsWith("Arrow")) return { Up: "↑", Down: "↓", Left: "←", Right: "→" }[code.slice(5)] || code;
  if (/^F\d{1,2}$/.test(code)) return code;
  return code;
}

function refreshKeycap() {
  els.pttKey.querySelector(".keycap__val").textContent = keyLabel(state.settings.key);
}

/* ── сочетания клавиш для горячей кнопки микрофона ── */

const MOD_NAME = { Shift: "Shift", Control: "Ctrl", Alt: "Alt" };

/** Превращает нажатие в строку вида "ControlRight+Insert". */
function comboFromEvent(e) {
  // сначала нажимают модификатор — ждём основную клавишу
  if (["Shift", "Control", "Alt", "Meta"].includes(e.key) && e.code === e.key) return null;
  const parts = [];
  if (e.ctrlKey) parts.push(sideOf("Control", e) === 2 ? "ControlRight" : "Control");
  if (e.shiftKey) parts.push(sideOf("Shift", e) === 2 ? "ShiftRight" : "Shift");
  if (e.altKey) parts.push(sideOf("Alt", e) === 2 ? "AltRight" : "Alt");
  parts.push(e.code || codeFromKey(e.key) || e.key);
  return parts.join("+");
}

/** Какая сторона модификатора нажата: 1 — левая, 2 — правая. */
function sideOf(base, e) {
  // если это событие самого модификатора, его location точный
  if (e.code === base + "Left") return 1;
  if (e.code === base + "Right") return 2;
  return modSide[base];
}

function comboMatches(e, combo) {
  if (!combo) return false;
  const parts = combo.split("+");
  const key = parts[parts.length - 1];
  const mods = parts.slice(0, -1);
  if (e.code !== key) return false;
  for (const [base, flag] of [["Control", "ctrlKey"], ["Shift", "shiftKey"], ["Alt", "altKey"]]) {
    // какая сторона требуется: 0 — не нужен, 1 — левая, 2 — правая
    const want = mods.includes(base + "Right") ? 2 : mods.includes(base) ? 1 : 0;
    if (e[flag] !== (want > 0)) return false;
    // и она должна совпадать: «Правый Ctrl» не должен срабатывать на левом
    if (want && sideOf(base, e) !== want) return false;
  }
  return true;
}

function comboLabel(combo) {
  if (!combo) return "не задана";
  return combo.split("+").map((p) => {
    if (p === "ControlRight") return "Правый Ctrl";
    if (p === "ShiftRight") return "Правый Shift";
    if (p === "AltRight") return "Правый Alt";
    return MOD_NAME[p] || keyLabel(p);
  }).join(" + ");
}

function hotkeyUsable() {
  // в режиме «удержание» микрофоном и так управляют удержанием — не мешаем
  return state.settings.mode !== "ptt";
}

function refreshHotkeyCap() {
  // в режиме удержания прячем поле целиком, а не гасим
  els.hotkeyField.hidden = !hotkeyUsable();
  els.micHotkey.querySelector(".keycap__val").textContent =
    comboLabel(state.settings.hotkey);
}

/* ── что браузер реально применил к звуку ── */

function renderMicStatus() {
  const f = voice.appliedFeatures();
  if (!f) { els.micStatus.hidden = true; return; }
  els.micStatus.hidden = false;

  const row = (name, actual, requested) => {
    let cls = "", state;
    if (actual === undefined) { cls = "is-off"; state = "браузер не сообщает"; }
    else if (actual) { state = "включено"; }
    else { cls = "is-off"; state = "выключено"; }
    if (requested && actual === false) cls = "is-warn";
    return `<div class="mstat ${cls}"><span>${name}</span><b>${state}</b></div>`;
  };

  els.micStatusRows.innerHTML =
    row("Подавление эха", f.echoCancellation, state.settings.echo) +
    row("Шумоподавление", f.noiseSuppression, state.settings.noise) +
    row("Авто-усиление", f.autoGainControl, state.settings.agc) +
    `<div class="mstat"><span>Частота звука</span><b>${
      f.sampleRate ? Math.round(f.sampleRate / 100) / 10 + " кГц" : "—"}</b></div>`;
}

/* ── разблокировка звука ──
   Браузер держит звук на паузе, пока пользователь ничего не нажимал, и снова
   замораживает при уходе вкладки в фон. Без этого собеседника молча не слышно. */
for (const ev of ["pointerdown", "keydown", "touchstart"]) {
  document.addEventListener(ev, () => voice.unlockAudio(), { passive: true });
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) voice.unlockAudio();
});
window.addEventListener("focus", () => voice.unlockAudio());

/* ── полоса «звук не идёт» ──
   Следим за состоянием связи: перерисовываем вместе с участниками и
   раз в несколько секунд, потому что браузер сам о сбое не сообщает. */
els.voiceWarnClose?.addEventListener("click", () => {
  warnDismissed = true;
  els.voiceWarn.hidden = true;
});
els.voiceWarnFix?.addEventListener("click", () => {
  els.voiceWarnText.innerHTML = "";
  const pre = document.createElement("p");
  pre.className = "voice-warn__pre";
  pre.textContent = WARN_HOWTO;
  els.voiceWarnText.appendChild(pre);
});
setInterval(() => {
  if (els.app.hidden) return;
  voice.retryStalled();
  updateVoiceWarn();
}, 3000);

/* ── фокус после клика мышью ──
   Браузер по умолчанию активирует кнопку по Пробелу, если на ней стоит фокус.
   Из-за этого после клика по «Микрофон» Пробел снова нажимал её — микрофон
   «переключался на Пробел», хотя назначена горячая клавиша. После клика мышью
   фокус снимаем; работа с клавиатуры (Tab + Enter) при этом сохраняется. */
document.addEventListener("pointerup", (e) => {
  const btn = e.target.closest?.("button");
  if (btn && document.activeElement === btn) btn.blur();
});

/* ─────────────────── утилиты ─────────────────── */

function initials(name) {
  return name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function linkify(text) {
  return text.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noreferrer">$1</a>');
}

// var, а не let: функцию toast() дёргают раньше, чем выполняется этот блок,
// и let дал бы ошибку «Cannot access before initialization»
var toastTimer;
function toast(text, kind = "") {
  els.toast.textContent = text;
  els.toast.className = "toast " + kind;
  els.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (els.toast.hidden = true), 3200);
}

/* ─────────────────── старт ─────────────────── */

els.loginName.value = state.name;
sessionStorage.setItem("ns.sid", state.sid);
applyTheme(localStorage.getItem("ns.theme") ||
  (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
// сперва узнаём, кто мы и с какого устройства, потом подтягиваем профиль
// и настройки — и только после этого решаем: входить сразу или спросить
profileReady = initIdentity().then(loadFromServer).finally(() => {
  if (state.profile.name) els.loginName.value = state.profile.name;
  refreshProfileUi();
  tryAutoLogin();
});

/* Если имя уже настроено, входа не спрашиваем: заходим сразу в тот канал,
   который был в прошлый раз, либо в первый по списку. */
async function tryAutoLogin() {
  if (!state.profile.name || els.app.hidden === false) return;
  if (sessionStorage.getItem("ns.welcomed") === "1") return; // уже показывали окно входа
  try {
    const info = await loadInfo();
    const remembered = sessionStorage.getItem("ns.channel");
    const wanted = info.channels.some((c) => c.id === remembered) ? remembered : null;
    els.loginChannel.value = wanted || info.channels[0].id;
  } catch {
    return; // сервер не ответил — покажем окно входа, там разберёмся
  }
  els.loginName.value = state.profile.name;
  els.loginForm.requestSubmit();
}
loadInfo();
// пока сидим на экране входа, место может освободиться — обновляем чаще
setInterval(() => { if (els.app.hidden) loadInfo(); }, 5000);

// точка входа для отладки: в консоли браузера доступно netspeak.voice.peers
window.netspeak = { state, voice, device, initIdentity };