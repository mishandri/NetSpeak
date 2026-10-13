/* NetSpeak — голосовой движок: микрофон, эхо и WebRTC-связи между участниками.
   Работает строго по локальной сети: iceServers пустой, только свои адреса. */

const RTC_CONFIG = {
  iceServers: [],
  bundlePolicy: "max-bundle",
};

// Демонстрацию экрана смотрит весь канал. При 8 участниках это 7 видеопоток,
// поэтому ужимаем картинку и битрейт — иначе Wi-Fi и Wi-Fi-роутеры не вывозят.
const SCREEN_BITRATE = 900 * 1000;   // ~900 кбит/с на человека
const SCREEN_MAX_WIDTH = 960;
const SCREEN_FPS = 10;
const AUDIO_BITRATE = 48 * 1000;     // достаточно для голоса, экономим сеть

// Сколько ждём, прежде чем признать: связи нет. Обмен адресами на домашней
// сети укладывается в пару секунд; если за десять не вышло — почти наверняка
// не пускает брандмауэр, и молчать дальше незачем.
const WAIT_LINK = 10;

export class Voice {
  constructor({ onState, onPeer, onError }) {
    this.onState = onState || (() => {});
    this.onPeer = onPeer || (() => {});
    this.onError = onError || (() => {});
    this.peers = new Map(); // id -> {pc, stream, audio, gain, analyser, speaking}
    this.ctx = null;
    this.mic = null; // MediaStream
    this.track = null;
    this.localStream = null;
    this.screenStream = null;
    this.screenTrack = null;
    this.enabled = false;
    this.muted = false;
    this.deafened = false;
    this.settings = {
      echo: true,        // браузерное подавление эха (только вкл/выкл)
      noise: true,       // браузерное шумоподавление
      autoGain: true,    // браузерное авто-усиление
      gain: 100,         // громкость микрофона, %
      highpass: 70,      // обрезка низких частот, Гц (гул, вибрации)
      gate: 6,           // порог шума: тише — считаем шумом, 0 = выключено
      leveling: 40,      // выравнивание громкости, 0 = выключено
      inputDevice: "",
      outputDevice: "",
      ptt: false,
    };
    this.selfId = null;
    this._speaking = false;
    this._volumes = new Map();
    // связи, которые уже пересобирали. Пометка живёт не на самой связи, а
    // здесь: пересоздавая связь, мы получаем новый объект, и пометка на нём
    // обнулилась бы — а тогда перебор шёл бы по кругу, без остановки.
    this._retried = new Set();
    // с какого момента связь впервые не поднялась. Тоже отдельно от самой
    // связи: пересоздание сбрасывает её счётчик, и предупреждение мигало бы
    // между «ждём» и «не вышло».
    this._failedSince = new Map();
    this._blank = null;
    this._blankStream = null;
    this.micState = "pending"; // pending | granted | denied
    this.lastMicError = null;
  }

  /* ---------- микрофон ---------- */
  // Пытается получить доступ к микрофону. true — доступ есть.
  async ensureMic() {
    if (this.micState === "granted") return true;
    try {
      await this.init();
      this.micState = "granted";
      return true;
    } catch (err) {
      this.micState = "denied";
      this.lastMicError = err;
      return false;
    }
  }

  resetPeers() {
    for (const id of [...this.peers.keys()]) this.dropPeer(id);
  }

  async init() {
    if (this.track) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("Браузер не умеет доступ к микрофону");
    }
    this.mic = await this._grab();
    this.track = this.mic.getAudioTracks()[0];
    this._ctx();
    this._buildChain();
    this._watchLevel();
    this.enabled = true;
    // дорожка начинает выключенной — флаг muted обязан совпадать с реальностью,
    // иначе первое нажатие «Микрофон» гасит его вместо включения
    this.muted = true;
    this.outTrack.enabled = false;
  }

  /**
   * Микрофон действительно звучит?
   *
   * Переключатель «не заглушен» — это ещё не значит, что в сеть уходит
   * звук: дорожка может быть выключена, а цепочка — молчать. Показывать
   * надо настоящее состояние, иначе человек видит «Микрофон вкл» и
   * недоумевает, почему его не слышно.
   */
  micLive() {
    if (!this.track) return false;
    if (this.muted) return false;
    if (this.outTrack && !this.outTrack.enabled) return false;
    return this.micState === "granted";
  }

  _constraints() {
    return {
      audio: {
        echoCancellation: this.settings.echo,
        noiseSuppression: this.settings.noise,
        autoGainControl: this.settings.autoGain,
        ...(this.settings.inputDevice ? { deviceId: this.settings.inputDevice } : {}),
      },
      video: false,
    };
  }

  /**
   * Открыть микрофон, попутно разбираясь с настройками из чужой машины.
   *
   * Настройки приложения хранятся на сервере — общие для всех, кто к нему
   * подключён. Но микрофоны у компьютеров разные, и номер устройства,
   * сохранённый на одном, на другом просто не существует. Браузер на такой
   * просьбе отвечает отказом, и микрофон не открывается вовсе: человек видит
   * «Микрофон вкл» (там стоит переключатель, а не реальное состояние
   * дорожки), в сеть уходит пустота, и собеседника не слышно.
   *
   * Поэтому: сперва проверяем, есть ли такой микрофон здесь. Нет — берём
   * тот, что есть, и говорим об этом прямо.
   */
  async _grab() {
    const wanted = this.settings.inputDevice;
    if (wanted && !(await this._hasDevice(wanted))) {
      this.settings.inputDevice = "";
      this.onError("В настройках был выбран микрофон, которого на этом "
        + "компьютере нет — беру микрофон по умолчанию");
    }
    try {
      return await navigator.mediaDevices.getUserMedia(this._constraints());
    } catch (err) {
      // вдруг список устройств обманул — пробуем вовсе без указания
      if (this.settings.inputDevice) {
        this.settings.inputDevice = "";
        try {
          const mic = await navigator.mediaDevices.getUserMedia(this._constraints());
          this.onError("Выбранный микрофон не открылся — беру микрофон по умолчанию");
          return mic;
        } catch (_) { /* падаем на исходную ошибку */ }
      }
      throw this._explain(err);
    }
  }

  /** Есть ли на этой машине микрофон с таким номером. */
  async _hasDevice(deviceId) {
    try {
      const list = await navigator.mediaDevices.enumerateDevices();
      return list.some((d) => d.kind === "audioinput" && d.deviceId === deviceId);
    } catch (_) {
      return true;   // не смогли узнать — не мешаем
    }
  }

  /** Человеческое объяснение вместо английских кодов. */
  _explain(err) {
    const name = err && (err.name || err.code) || "";
    if (name === "NotAllowedError" || name === "PermissionDeniedError") {
      return new Error("доступ к микрофону запрещён — разрешите его в Windows "
        + "или в браузере");
    }
    if (name === "NotFoundError" || name === "DevicesNotFoundError") {
      return new Error("микрофон не найден");
    }
    if (name === "OverconstrainedError" || name === "ConstraintNotSatisfiedError") {
      return new Error("выбранный микрофон недоступен");
    }
    if (name === "NotReadableError" || name === "TrackStartError") {
      return new Error("микрофон занят другой программой");
    }
    return err;
  }

  /**
   * Пересобирает обработку звука. Браузер применяет подавление эха и шумоподавление
   * только в момент получения потока, поэтому смена галочек требует нового
   * запроса к микрофону — иначе настройки молча ничего не делают.
   */
  async reconfigure() {
    if (!this.track) return false;
    const wasOn = !this.muted;
    const old = this.mic;

    const stream = await this._grab();
    this.mic = stream;
    this.track = stream.getAudioTracks()[0];
    this._buildChain();
    this.outTrack.enabled = wasOn;

    for (const peer of this.peers.values()) {
      const sender = peer.pc.getSenders().find((s) => s.track?.kind === "audio");
      if (sender) await sender.replaceTrack(this.outTrack).catch(() => {});
    }
    if (old) for (const t of old.getTracks()) t.stop();
    this.onState();
    return true;
  }

  /**
   * Цепочка обработки: микрофон → срез низких → порог шума → выравнивание →
   * громкость → на выход в сеть. Всё, что можно крутить ползунками, сделано
   * здесь, потому что у браузера эти параметры — только «да/нет».
   */
  _buildChain() {
    const ctx = this._ctx();
    const src = ctx.createMediaStreamSource(this.mic);

    const hp = ctx.createBiquadFilter();
    hp.type = "highpass";
    hp.Q.value = 0.7;

    const gate = ctx.createGain();
    const comp = ctx.createDynamicsCompressor();
    comp.knee.value = 8;
    comp.attack.value = 0.004;
    comp.release.value = 0.18;

    const master = ctx.createGain();
    const dest = ctx.createMediaStreamDestination();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.35;

    src.connect(hp);
    hp.connect(analyser);   // замеряем «как есть», до приглушения
    analyser.connect(gate);
    gate.connect(comp);
    comp.connect(master);
    master.connect(dest);
    // Замер после всей обработки, у самого выхода в сеть. Без него
    // невозможно отличить «микрофон молчит» от «микрофон слышит, но порог
    // закрыл выход» — а это разные поломки с одинаковым симптомом:
    // собеседника не слышно.
    const out = ctx.createAnalyser();
    out.fftSize = 512;
    out.smoothingTimeConstant = 0.3;
    master.connect(out);
    // НЕ соединяем с ctx.destination — иначе будет самослышание

    this.chain = { src, hp, gate, comp, master, analyser, out, dest };
    this.outTrack = dest.stream.getAudioTracks()[0];
    this._applyAudioSettings();
  }

  /** Применяет ползунки к живой цепочке. */
  _applyAudioSettings() {
    const c = this.chain;
    if (!c) return;
    const s = this.settings;
    c.hp.frequency.value = s.highpass;

    const leveling = Number(s.leveling) || 0;
    c.comp.ratio.value = leveling > 0 ? 1 + (leveling / 100) * 8 : 1;
    c.comp.threshold.value = leveling > 0 ? -46 + (leveling / 100) * 30 : 0;

    c.master.gain.value = (Number(s.gain) || 0) / 100;
  }

  /**
   * Что браузер действительно применил. Не верьте галочкам: браузер может
   * проигнорировать запрос (например, на некоторых устройствах).
   */
  appliedFeatures() {
    if (!this.track) return null;
    const s = this.track.getSettings ? this.track.getSettings() : {};
    return {
      echoCancellation: s.echoCancellation,
      noiseSuppression: s.noiseSuppression,
      autoGainControl: s.autoGainControl,
      deviceId: s.deviceId,
      sampleRate: s.sampleRate,
      channelCount: s.channelCount,
    };
  }

  _ctx() {
    if (this.ctx) return this.ctx;
    const AC = window.AudioContext || window.webkitAudioContext;
    this.ctx = new AC();
    if (this.ctx.state === "suspended") this.ctx.resume().catch(() => {});
    return this.ctx;
  }

  /**
   * Возвращает звук, если браузер его «заморозил».
   *
   * Chrome держит AudioContext на паузе, пока пользователь не сделал ни одного
   * действия на странице, и снова замораживает при уходе вкладки в фон.
   * После этого звук пропадает молча, и обычное переключение его не лечит —
   * помогает только случайное нажатие. Поэтому явно будим и перезапускаем
   * воспроизведение при любом действии пользователя.
   */
  unlockAudio() {
    const ctx = this.ctx;
    if (!ctx) return;
    if (ctx.state !== "running") {
      // звук спит — сначала будим контекст, воспроизведение запустится само
      ctx.resume().catch(() => {});
      return;
    }
    // контекст будим в любом случае — им меряем уровень и «говорит»
    this.ctx.resume().catch(() => {});
    for (const peer of this.peers.values()) {
      const el = peer.audio;
      if (el && el.paused) el.play().catch(() => {});
    }
  }

  setMic(on) {
    this.muted = !on;
    if (this.outTrack) this.outTrack.enabled = on;
    this.onState();
    for (const peer of this.peers.values()) this._syncAudio(peer);
    this.unlockAudio();
  }

  setDeaf(on) {
    this.deafened = on;
    if (on) this.setMic(false);
    for (const peer of this.peers.values()) this._syncAudio(peer);
    // нажатие на «Звук» — жест пользователя; используем его, чтобы разбудить звук
    this.unlockAudio();
    this.onState();
  }

  _syncAudio(peer) {
    const vol = this.deafened ? 0 : (peer.volume ?? 1);
    if (peer.gainNode) peer.gainNode.gain.value = vol;
    if (peer.audio) {
      // 100% — это полная громкость. Выше элемент не умеет, так что 150%
      // играет как 100%: лучше так, чем сделать обычную громкость тише
      // прежнего, потому что делить на 1.5 «на всякий случай».
      peer.audio.volume = Math.min(1, vol);
      peer.audio.muted = vol === 0;
    }
  }

  /* индикация «кто говорит» и приглушение фонового шума */
  _watchLevel() {
    const analyser = this.chain?.analyser;
    if (!analyser) return;
    const buf = new Uint8Array(analyser.frequencyBinCount);
    let silence = 0;
    const tick = () => {
      if (!this.ctx) return;
      analyser.getByteFrequencyData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const level = Math.sqrt(sum / buf.length);

      // порог шума: всё тише считаем фоном и приглушаем
      const gate = Number(this.settings.gate) || 0;
      const open = gate === 0 || level >= gate;
      if (this.chain) {
        this.chain.gate.gain.setTargetAtTime(open ? 1 : 0, this.ctx.currentTime, 0.03);
      }

      // Если на входе явно громко, а на выходе тишина — порог закрылся не
      // по делу и в сеть уходит пустота. Снаружи это неотличимо от
      // «микрофон не работает», хотя он работает. Держим выход открытым:
      // лишний фон в тишине лучше, когда человека вообще не слышно.
      const outAnalyser = this.chain?.out;
      let outLevel = 0;
      if (outAnalyser) {
        const obuf = new Uint8Array(outAnalyser.frequencyBinCount);
        outAnalyser.getByteFrequencyData(obuf);
        let s2 = 0;
        for (let i = 0; i < obuf.length; i++) s2 += obuf[i] * obuf[i];
        outLevel = Math.sqrt(s2 / obuf.length);
      }
      this.inputLevel = level;
      this.outputLevel = outLevel;
      if (level >= 25 && outLevel < 1) {
        this._silentOut = (this._silentOut || 0) + 1;
        if (this._silentOut > 30 && gate > 0) {
          this._silentOut = 0;
          this.chain.gate.gain.setTargetAtTime(1, this.ctx.currentTime, 0.02);
          this.onError("Порог шума закрывал выход — отключил его, "
            + "иначе вас было бы не слышно");
        }
      } else {
        this._silentOut = 0;
      }

      const talking = !this.muted && this.outTrack?.enabled && open && level > 26;
      if (talking !== this._speaking) {
        this._speaking = talking;
        this.onState({ speaking: talking });
      }
      // отпускаем «говорящего» через 400мс тишины
      silence = talking ? 0 : silence + 1;
      if (!talking && silence === 24) this.onState({ speaking: false });
      this.level = level;
      requestAnimationFrame(tick);
    };
    tick();
  }

  /* ---------- захват экрана ---------- */
  async startScreen() {
    this.screenStream = await navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: SCREEN_FPS, width: { max: SCREEN_MAX_WIDTH } },
      audio: false,
    });
    this.screenTrack = this.screenStream.getVideoTracks()[0];
    this.screenTrack.addEventListener("ended", () => this.stopScreen());
    // трек отправителя уже создан при connectPeer — просто подменяем картинку
    for (const peer of this.peers.values()) {
      const sender = peer.pc.getSenders().find((s) => s.track?.kind === "video");
      if (sender) await sender.replaceTrack(this.screenTrack);
    }
    this._tuneSenders();
    return this.screenTrack;
  }

  /** Ограничивает битрейт: аудио всегда, видео — только когда идёт экран. */
  _tuneSenders() {
    for (const peer of this.peers.values()) {
      for (const sender of peer.pc.getSenders()) {
        if (!sender.track) continue;
        const isVideo = sender.track.kind === "video";
        if (isVideo && !this.screenTrack) continue;
        const params = sender.getParameters();
        if (!params.encodings || !params.encodings.length) continue;
        params.encodings[0].maxBitrate = isVideo ? SCREEN_BITRATE : AUDIO_BITRATE;
        sender.setParameters(params).catch(() => {});
      }
    }
  }

  async stopScreen() {
    this.screenTrack = null;
    if (this.screenStream) {
      for (const t of this.screenStream.getTracks()) t.stop();
      this.screenStream = null;
    }
    for (const peer of this.peers.values()) {
      const sender = peer.pc.getSenders().find((s) => s.track?.kind === "video");
      if (sender) await sender.replaceTrack(this._blank);
    }
  }

  get isSharing() {
    return !!this.screenTrack;
  }

  _blankVideoTrack() {
    if (this._blank) return this._blank;
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 2;
    this._blankStream = canvas.captureStream(1);
    this._blank = this._blankStream.getVideoTracks()[0];
    return this._blank;
  }

  /* ---------- связи между участниками ---------- */
  setSelf(id) {
    this.selfId = id;
  }

  async connectPeer(peerId, initiator) {
    if (peerId === this.selfId || this.peers.has(peerId)) return;
    const pc = new RTCPeerConnection(RTC_CONFIG);
    const peer = {
      id: peerId,
      pc,
      audio: null,
      gainNode: null,
      analyser: null,
      volume: this._volumes?.get(peerId) ?? 1,
      speaking: false,
      mutedRemote: false,
      video: null,
      pending: [], // кандидаты, пришедшие раньше описания сессии
      since: Date.now(),   // с какого момента ждём эту связь
    };

    // исходящие треки: обработанный микрофон + всегда готовый видео-слот
    if (this.outTrack) pc.addTrack(this.outTrack, this.chain.dest.stream);
    pc.addTrack(this._blankVideoTrack(), this._blankStream);

    pc.ontrack = (ev) => {
      if (ev.track.kind === "audio") {
        const stream = ev.streams[0] || new MediaStream([ev.track]);
        peer.stream = stream;
        // Раньше поток играл дважды: через WebAudio (к ctx.destination) и
        // через отдельный элемент <audio>. Два независимых пути к одному
        // потоку — источник странных проблем: на части систем второй путь
        // гасит первый, и в итоге не слышно ничего. Оставим один, через
        // WebAudio: там же замеряем уровень для индикатора и крутим
        // громкость собеседника.
        this._graph(peer, stream);
        // звук мог уснуть — будим, раз он уже готов
        this.unlockAudio();
      } else if (ev.track.kind === "video") {
        const stream = ev.streams[0] || new MediaStream([ev.track]);
        peer.video = stream;
        this.onPeer(peerId, { video: stream });
      }
    };

    pc.onicecandidate = (ev) => {
      if (ev.candidate) this._signal(peerId, { candidate: ev.candidate });
    };

    pc.onconnectionstatechange = () => {
      if (pc.connectionState === "connected") {
        // связались — человек появился в сети заново, право на перебор
        // возвращаем: может снова понадобиться
        this._retried.delete(peerId);
        this._failedSince.delete(peerId);
        return;
      }
      if (["failed", "closed"].includes(pc.connectionState)) {
        this.dropPeer(peerId);
      }
    };

    this.peers.set(peerId, peer);
    this._tuneSenders();

    // предлагает только «меньший» id — так не бывает двух одновременных offer
    if (!initiator) return;
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    this._signal(peerId, { sdp: pc.localDescription });
  }

  /**
   * Приём звука собеседника.
   *
   * Раньше звук выводился через WebAudio прямо в ctx.destination. Это самый
   * капризный путь: он зависит от состояния общего AudioContext, который
   * браузер любит замораживать, и в окне на WebView2 ведёт себя особенно
   * непредсказуемо — вплоть до полной тишины при работающей связи.
   *
   * Теперь звук выводит обычный <audio>, как в любом плеере: путь прямой и
   * надёжный, громкость крутится на элементе. WebAudio остаётся, но только
   * для замера уровня — точки «говорит». Ничего лишнего в колонки не идёт,
   * поэтому самослышания не возникает.
   */
  _graph(peer, stream) {
    const ctx = this._ctx();
    let analyser = null;
    try {
      const src = ctx.createMediaStreamSource(stream);
      analyser = ctx.createAnalyser();
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.4;
      src.connect(analyser);
      // дальше намеренно никуда: измеряем и всё
    } catch (err) {
      this.onError("Не удалось настроить приём звука: " + (err && err.message || err));
    }
    peer.gainNode = null;
    peer.analyser = analyser;
    peer.audio = this._plainAudio(peer, stream);
    if (!analyser) return;          // без замера всё равно слышно — не молчим из-за точки «говорит»

    const buf = new Uint8Array(analyser.frequencyBinCount);
    const peerId = peer.id;
    const tick = () => {
      if (!this.peers.has(peerId) || !this.peers.get(peerId).analyser) return;
      analyser.getByteFrequencyData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const level = Math.sqrt(sum / buf.length);
      const talking = !peer.mutedRemote && level > 30;
      if (talking !== peer.speaking) {
        peer.speaking = talking;
        this.onPeer(peerId, { speaking: talking });
      }
      requestAnimationFrame(tick);
    };
    tick();
    this._syncAudio(peer);
  }

  /**
   * Состояние связи для интерфейса: «ок» / «ждём» / «не вышло».
   *
   * Зачем: пока обмен адресами не начался, браузер честно стоит в
   * состоянии «new» — и ни ошибки, ни обрыва, ни звука. Выглядит это так,
   * будто всё в порядке, а человек молчит и не понимает почему. Теперь
   * есть чем это показать.
   *
   *   ждём     — меньше ЖДАТИ_LINK секунд, всё штатно
   *   не вышло — ждём дольше: почти наверняка не пускает брандмауэр
   *   ок       — соединение есть
   */
  linkState(peerId) {
    const peer = this.peers.get(peerId);
    if (!peer) { this._failedSince.delete(peerId); return { code: "нет", waited: 0 }; }
    const cs = peer.pc.connectionState;
    if (cs === "connected") {
      // связь поднялась — прошлое неудачное забываем
      this._failedSince.delete(peerId);
      return { code: "ок", waited: 0, cs };
    }
    // отсчёт с первой неудачи, а не с текущей связи: иначе после пересборки
    // счётчик начинался заново и предупреждение мигало
    const failedAt = this._failedSince.get(peerId) ?? null;
    if (failedAt === null) {
      this._failedSince.set(peerId, Date.now());
      return { code: "ждём", waited: 0, cs };
    }
    const waited = Math.round((Date.now() - failedAt) / 1000);
    if (cs === "failed" || cs === "disconnected" || waited >= WAIT_LINK) {
      return { code: "не вышло", waited, cs };
    }
    return { code: "ждём", waited, cs };
  }

  /**
   * Пересобрать связь, если она не поднялась.
   *
   * Случайно: обмен сообщениями мог потеряться, и пара так и осталась
   * ждать. Один раз пробуем заново — этого хватает, а повторять бесконечно
   * мы не станем, чтобы не мучить сеть.
   */
  retryStalled(now = Date.now()) {
    const stuck = [];
    for (const [id, peer] of this.peers) {
      if (this._retried.has(id)) continue;
      if (peer.pc.connectionState === "connected") continue;
      if (this.linkState(id).code !== "не вышло") continue;
      this._retried.add(id);
      stuck.push(id);
    }
    for (const id of stuck) {
      const peer = this.peers.get(id);
      if (!peer) continue;
      // создаём заново, но одним шансом: тот, кто предлагал, предлагает снова
      const initiator = peer.id < this.selfId;
      try { peer.pc.close(); } catch (_) {}
      this.peers.delete(id);
      this.connectPeer(id, initiator);
    }
    return stuck;
  }

  /** Обычный вывод звука: так надёжнее всего. */
  _plainAudio(peer, stream) {
    const audio = new Audio();
    audio.srcObject = stream;
    audio.autoplay = true;
    audio.volume = this.deafened ? 0 : Math.min(1, peer.volume ?? 1);
    audio.play?.().catch(() => {});
    return audio;
  }

  /**
   * Что видно по связи с участником.
   *
   * Нужен, чтобы «звука нет» перестало быть гаданием. Раньше можно было
   * только гадать: точка «говорит» молчит и непонятно — не дошло ли
   * соединение, пришёл ли звук или просто выкручен ползунок.
   *
   *   conn      — соединение: new / connecting / connected / failed
   *   ice       — обмен адресами
   *   bytes     — сколько байт звука реально пришло с этого человека
   *   уровень   — насколько громко сейчас звучит (0..255)
   *   играем    — куда выведен звук: web — через WebAudio, прямо — без него
   */
  async linkReport(peerId) {
    const peer = this.peers.get(peerId);
    if (!peer) return { conn: "нет связи", ice: "-", bytes: 0, level: 0, plays: "-" };
    let bytes = 0, level = 0;
    try {
      const stats = await peer.pc.getStats();
      stats.forEach((s) => {
        if (s.type === "inbound-rtp" && s.kind === "audio") {
          bytes = s.bytesReceived || 0;
          level = Math.round((s.audioLevel || 0) * 255);
        }
      });
    } catch (err) {
      /* статистика может быть недоступна — это не повод ломать отчёт */
    }
    let local = 0;
    if (peer.analyser) {
      const buf = new Uint8Array(peer.analyser.frequencyBinCount);
      peer.analyser.getByteFrequencyData(buf);
      for (let i = 0; i < buf.length; i++) local += buf[i] * buf[i];
      local = Math.round(Math.sqrt(local / buf.length));
    }
    return {
      conn: peer.pc.connectionState,
      ice: peer.pc.iceConnectionState,
      bytes,
      level,
      local: local,
      gain: peer.gainNode ? peer.gainNode.gain.value : null,
      plays: peer.gainNode ? "web" : (peer.audio ? "прямо" : "нет"),
      outgoing: !!(this.outTrack && this.outTrack.enabled),
      ctx: this.ctx ? this.ctx.state : "нет",
    };
  }

  setVolume(peerId, value) {
    this._volumes = this._volumes || new Map();
    this._volumes.set(peerId, value);
    const peer = this.peers.get(peerId);
    if (peer) {
      peer.volume = value;
      this._syncAudio(peer);
    }
  }

  setRemoteMuted(peerId, muted) {
    const peer = this.peers.get(peerId);
    if (peer) {
      peer.mutedRemote = muted;
      if (!muted && !this.deafened) peer.speaking = false;
    }
  }

  async handleSignal(from, data) {
    if (!data.sdp && !data.candidate) return;
    let peer = this.peers.get(from);
    if (!peer) {
      // нас опередили: создаём соединение пассивно и ждём их offer
      await this.connectPeer(from, false);
      peer = this.peers.get(from);
    }
    if (!peer) return;
    const pc = peer.pc;
    try {
      if (data.sdp) {
        await pc.setRemoteDescription(new RTCSessionDescription(data.sdp));
        // ICE-кандидаты могли прийти раньше SDP — применяем их после
        for (const c of peer.pending.splice(0)) {
          pc.addIceCandidate(new RTCIceCandidate(c)).catch(() => {});
        }
        if (data.sdp.type === "offer") {
          const answer = await pc.createAnswer();
          await pc.setLocalDescription(answer);
          this._signal(from, { sdp: pc.localDescription });
        }
      } else if (data.candidate) {
        if (pc.remoteDescription) {
          await pc.addIceCandidate(new RTCIceCandidate(data.candidate));
        } else {
          peer.pending.push(data.candidate);
        }
      }
    } catch (err) {
      console.warn("WebRTC", err);
    }
  }

  dropPeer(peerId) {
    const peer = this.peers.get(peerId);
    if (!peer) return;
    try {
      peer.pc.close();
    } catch (_) {}
    this.peers.delete(peerId);
    this.onPeer(peerId, null);
  }

  syncPeers(ids) {
    const set = new Set(ids);
    for (const id of [...this.peers.keys()]) {
      if (!set.has(id)) this.dropPeer(id);
    }
    for (const id of set) {
      if (id === this.selfId) continue;
      // инициатором назначаем клиента с меньшим id — гонки offer'ов не будет
      if (!this.peers.has(id)) this.connectPeer(id, String(this.selfId) < id);
    }
  }

  _signal(to, data) {
    this._send?.({ type: "signal", to, data });
  }

  close() {
    for (const id of [...this.peers.keys()]) this.dropPeer(id);
    if (this.screenStream) for (const t of this.screenStream.getTracks()) t.stop();
    this.screenStream = null;
    this.screenTrack = null;
    if (this.mic) for (const t of this.mic.getTracks()) t.stop();
    this.mic = null;
    this.track = null;
    this.outTrack = null;
    this.chain = null;
    this.muted = false;
    this.deafened = false;
    this.micState = "pending";
  }
}