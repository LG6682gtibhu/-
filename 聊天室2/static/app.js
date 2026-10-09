"use strict";

const STORAGE_KEYS = {
  userId: "lan-chat.user-id",
  nickname: "lan-chat.nickname",
  peerId: "lan-chat.peer-id",
  peerNickname: "lan-chat.peer-nickname",
};

const TIMING = {
  heartbeat: 2000,
  messagePoll: 1000,
  maxRetryDelay: 10000,
};

const state = {
  userId: sessionStorage.getItem(STORAGE_KEYS.userId),
  nickname: sessionStorage.getItem(STORAGE_KEYS.nickname),
  peer: null,
  onlineUsers: [],
  conversations: [],
  messageIds: new Set(),
  lastMessageId: 0,
  heartbeatTimer: null,
  messageTimer: null,
  conversationVersion: 0,
  heartbeatRetryDelay: TIMING.heartbeat,
  messageRetryDelay: TIMING.messagePoll,
  sending: false,
};

const dom = {
  loginView: document.querySelector("#login-view"),
  appView: document.querySelector("#app-view"),
  loginForm: document.querySelector("#login-form"),
  nicknameInput: document.querySelector("#nickname-input"),
  loginButton: document.querySelector("#login-button"),
  loginStatus: document.querySelector("#login-status"),
  myNickname: document.querySelector("#my-nickname"),
  logoutButton: document.querySelector("#logout-button"),
  connectionState: document.querySelector("#connection-state"),
  connectionText: document.querySelector("#connection-text"),
  onlineCount: document.querySelector("#online-count"),
  onlineList: document.querySelector("#online-list"),
  conversationList: document.querySelector("#conversation-list"),
  emptyConversation: document.querySelector("#empty-conversation"),
  conversationView: document.querySelector("#conversation-view"),
  peerNickname: document.querySelector("#peer-nickname"),
  peerStatus: document.querySelector("#peer-status"),
  messageList: document.querySelector("#message-list"),
  messageForm: document.querySelector("#message-form"),
  messageInput: document.querySelector("#message-input"),
  sendButton: document.querySelector("#send-button"),
  sendError: document.querySelector("#send-error"),
  mobileBack: document.querySelector("#mobile-back"),
};

/**
 * 统一请求后端。
 * 参数 path 是 /api/... 路径，options 与 fetch 的配置一致。
 */
async function api(path, options = {}) {
  const config = {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  };

  if (config.body && typeof config.body !== "string") {
    config.body = JSON.stringify(config.body);
  }

  const response = await fetch(path, config);
  const text = await response.text();
  let data = null;

  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!response.ok) {
    let message = "请求失败，请稍后重试";
    if (typeof data?.detail === "string") {
      message = data.detail;
    } else if (Array.isArray(data?.detail) && data.detail.length > 0) {
      message = data.detail.map((item) => item.msg).join("；");
    }
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }

  return data;
}

function setLoginStatus(message, kind = "") {
  dom.loginStatus.textContent = message;
  if (kind) {
    dom.loginStatus.dataset.kind = kind;
  } else {
    delete dom.loginStatus.dataset.kind;
  }
}

function setConnectionState(connectionState, message) {
  dom.connectionState.dataset.state = connectionState;
  dom.connectionText.textContent = message;
}

function showLoginView() {
  dom.appView.hidden = true;
  dom.loginView.hidden = false;
  dom.loginButton.disabled = false;
  document.body.classList.remove("chat-open");
}

function showAppView() {
  dom.loginView.hidden = true;
  dom.appView.hidden = false;
  dom.myNickname.textContent = state.nickname;
}

function saveSession() {
  sessionStorage.setItem(STORAGE_KEYS.userId, state.userId);
  sessionStorage.setItem(STORAGE_KEYS.nickname, state.nickname);
}

function clearSessionStorage() {
  Object.values(STORAGE_KEYS).forEach((key) => sessionStorage.removeItem(key));
}

function clearTimers() {
  window.clearTimeout(state.heartbeatTimer);
  window.clearTimeout(state.messageTimer);
  state.heartbeatTimer = null;
  state.messageTimer = null;
}

/**
 * 创建或恢复当前用户。
 * userId 和 nickname 由登录表单或 sessionStorage 提供。
 */
async function startSession(userId, nickname) {
  const payload = await api("/api/session", {
    method: "POST",
    body: { user_id: userId || null, nickname },
  });

  state.userId = payload.user.id;
  state.nickname = payload.user.nickname;
  saveSession();
  applyLiveData(payload);
  showAppView();
  setConnectionState("online", "已连接");
  scheduleHeartbeat(0);
  restorePreviousPeer();
}

function applyLiveData(payload) {
  state.onlineUsers = payload.online_users || [];
  state.conversations = payload.conversations || [];
  updateSelectedPeerStatus();
  renderPeopleLists();
}

function getPeerFromLiveData(peerId) {
  const onlinePeer = state.onlineUsers.find((user) => user.id === peerId);
  if (onlinePeer) {
    return { ...onlinePeer };
  }

  const conversation = state.conversations.find(
    (item) => item.peer.id === peerId
  );
  return conversation ? { ...conversation.peer } : null;
}

function updateSelectedPeerStatus() {
  if (!state.peer) {
    return;
  }

  const latestPeer = getPeerFromLiveData(state.peer.id);
  if (latestPeer) {
    state.peer = latestPeer;
  } else {
    state.peer.is_online = false;
  }

  updateChatHeader();
  updateComposer();
}

function restorePreviousPeer() {
  const peerId = sessionStorage.getItem(STORAGE_KEYS.peerId);
  const peerNickname = sessionStorage.getItem(STORAGE_KEYS.peerNickname);
  if (!peerId || !peerNickname) {
    return;
  }

  const peer = getPeerFromLiveData(peerId) || {
    id: peerId,
    nickname: peerNickname,
    is_online: false,
  };
  selectPeer(peer);
}

function scheduleHeartbeat(delay = TIMING.heartbeat) {
  window.clearTimeout(state.heartbeatTimer);
  if (!state.userId) {
    return;
  }
  state.heartbeatTimer = window.setTimeout(runHeartbeat, delay);
}

/**
 * 定期告诉服务端“我还在线”，并取回最新用户与最近会话。
 */
async function runHeartbeat() {
  window.clearTimeout(state.heartbeatTimer);
  state.heartbeatTimer = null;

  if (!state.userId) {
    return;
  }

  try {
    const payload = await api("/api/heartbeat", {
      method: "POST",
      body: { user_id: state.userId },
    });
    state.heartbeatRetryDelay = TIMING.heartbeat;
    setConnectionState("online", "已连接");
    applyLiveData(payload);
  } catch (error) {
    if (error.status === 404) {
      clearTimers();
      clearSessionStorage();
      state.userId = null;
      state.nickname = null;
      showLoginView();
      setLoginStatus("会话已失效，请重新输入昵称", "error");
      return;
    }

    setConnectionState("offline", "连接中断，正在重试");
    state.heartbeatRetryDelay = Math.min(
      state.heartbeatRetryDelay * 1.5,
      TIMING.maxRetryDelay
    );
  } finally {
    if (state.userId) {
      scheduleHeartbeat(state.heartbeatRetryDelay);
    }
  }
}

function scheduleMessagePoll(delay = TIMING.messagePoll) {
  window.clearTimeout(state.messageTimer);
  if (!state.peer || !state.userId) {
    return;
  }
  const version = state.conversationVersion;
  state.messageTimer = window.setTimeout(
    () => pollMessages(version, false),
    delay
  );
}

/**
 * 拉取当前会话消息。
 * reset 为 true 时获取完整历史，否则只请求 lastMessageId 之后的新消息。
 */
async function pollMessages(version, reset) {
  window.clearTimeout(state.messageTimer);
  state.messageTimer = null;

  if (!state.peer || !state.userId || version !== state.conversationVersion) {
    return;
  }

  const peerId = state.peer.id;
  const params = new URLSearchParams({
    me: state.userId,
    peer: peerId,
  });

  if (!reset && state.lastMessageId > 0) {
    params.set("after_id", String(state.lastMessageId));
  }

  try {
    const payload = await api(`/api/messages?${params.toString()}`);
    if (version !== state.conversationVersion || state.peer?.id !== peerId) {
      return;
    }

    if (reset) {
      state.messageIds.clear();
      state.lastMessageId = 0;
      dom.messageList.replaceChildren();
    }

    appendMessages(payload.messages || []);
    state.messageRetryDelay = TIMING.messagePoll;
  } catch (error) {
    state.messageRetryDelay = Math.min(
      state.messageRetryDelay * 1.5,
      TIMING.maxRetryDelay
    );
    if (error.status === 404) {
      setSendError("该用户已不存在，无法读取这段历史");
    }
  } finally {
    if (version === state.conversationVersion && state.peer) {
      scheduleMessagePoll(state.messageRetryDelay);
    }
  }
}

function selectPeer(peer) {
  state.peer = {
    id: peer.id,
    nickname: peer.nickname,
    is_online: Boolean(peer.is_online),
  };
  sessionStorage.setItem(STORAGE_KEYS.peerId, state.peer.id);
  sessionStorage.setItem(STORAGE_KEYS.peerNickname, state.peer.nickname);

  state.conversationVersion += 1;
  state.lastMessageId = 0;
  state.messageIds.clear();
  state.messageRetryDelay = TIMING.messagePoll;
  dom.messageList.replaceChildren();
  setSendError("");

  dom.emptyConversation.hidden = true;
  dom.conversationView.hidden = false;
  document.body.classList.add("chat-open");
  updateChatHeader();
  updateComposer();
  renderPeopleLists();

  const version = state.conversationVersion;
  pollMessages(version, true);
}

function updateChatHeader() {
  if (!state.peer) {
    return;
  }

  dom.peerNickname.textContent = state.peer.nickname;
  dom.peerStatus.textContent = state.peer.is_online
    ? "在线"
    : "离线，仅可查看历史消息";
}

function updateComposer() {
  const canSend = Boolean(state.peer?.is_online) && !state.sending;
  dom.messageInput.disabled = !canSend;
  dom.sendButton.disabled = !canSend;
  dom.sendButton.textContent = state.sending ? "发送中" : "发送";
  dom.messageInput.placeholder = state.peer?.is_online
    ? "输入消息，Enter 发送，Shift + Enter 换行"
    : "对方离线，暂时不能发送消息";
}

function setSendError(message) {
  dom.sendError.textContent = message;
}

function makeAvatar(nickname) {
  const characters = Array.from(nickname.trim());
  return characters[0] || "?";
}

function shortUserId(userId) {
  return userId.slice(-4);
}

function createPersonRow({
  peer,
  detail,
  trailingText = "",
  selected = false,
}) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = "person-row";
  if (selected) {
    row.classList.add("is-selected");
  }

  const avatar = document.createElement("span");
  avatar.className = "avatar";
  avatar.textContent = makeAvatar(peer.nickname);

  const text = document.createElement("span");
  text.className = "person-text";
  const name = document.createElement("span");
  name.className = "person-name";
  name.textContent = peer.nickname;
  const detailText = document.createElement("span");
  detailText.className = "person-detail";
  detailText.textContent = detail;
  text.append(name, detailText);

  let trailing;
  if (trailingText) {
    trailing = document.createElement("span");
    trailing.className = "person-detail";
    trailing.textContent = trailingText;
  } else {
    trailing = document.createElement("span");
    trailing.className = "presence";
    if (peer.is_online) {
      trailing.classList.add("is-online");
    }
  }

  row.append(avatar, text, trailing);
  row.addEventListener("click", () => selectPeer(peer));
  return row;
}

function renderEmpty(container, message) {
  const empty = document.createElement("p");
  empty.className = "list-empty";
  empty.textContent = message;
  container.replaceChildren(empty);
}

function renderPeopleLists() {
  dom.onlineCount.textContent = String(state.onlineUsers.length);

  if (state.onlineUsers.length === 0) {
    renderEmpty(dom.onlineList, "暂时没有其他在线用户。");
  } else {
    const rows = state.onlineUsers.map((user) =>
      createPersonRow({
        peer: user,
        detail: `用户尾号 ${shortUserId(user.id)}`,
        selected: state.peer?.id === user.id,
      })
    );
    dom.onlineList.replaceChildren(...rows);
  }

  if (state.conversations.length === 0) {
    renderEmpty(dom.conversationList, "还没有历史会话。");
  } else {
    const rows = state.conversations.map((conversation) => {
      const preview =
        conversation.last_message.sender_id === state.userId ? "我：" : "";
      return createPersonRow({
        peer: conversation.peer,
        detail: `${preview}${conversation.last_message.content}`,
        trailingText: formatShortTime(conversation.last_message.created_at),
        selected: state.peer?.id === conversation.peer.id,
      });
    });
    dom.conversationList.replaceChildren(...rows);
  }
}

function formatShortTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }

  const now = new Date();
  const sameDay =
    date.getFullYear() === now.getFullYear() &&
    date.getMonth() === now.getMonth() &&
    date.getDate() === now.getDate();

  if (sameDay) {
    return date.toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  return `${date.getMonth() + 1}/${date.getDate()}`;
}

function formatMessageTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function appendMessages(messages) {
  const shouldStickToBottom =
    dom.messageList.scrollHeight -
      dom.messageList.scrollTop -
      dom.messageList.clientHeight <
    90;
  let addedOwnMessage = false;

  messages.forEach((message) => {
    if (state.messageIds.has(message.id)) {
      return;
    }

    state.messageIds.add(message.id);
    state.lastMessageId = Math.max(state.lastMessageId, message.id);
    const isOwn = message.sender_id === state.userId;
    if (isOwn) {
      addedOwnMessage = true;
    }

    const row = document.createElement("article");
    row.className = "message-row";
    if (isOwn) {
      row.classList.add("is-own");
    }

    const meta = document.createElement("p");
    meta.className = "message-meta";
    const senderName = isOwn ? "我" : state.peer?.nickname || "对方";
    meta.textContent = `${senderName} · ${formatMessageTime(message.created_at)}`;

    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    bubble.textContent = message.content;

    row.append(meta, bubble);
    dom.messageList.append(row);
  });

  if (addedOwnMessage || shouldStickToBottom) {
    dom.messageList.scrollTop = dom.messageList.scrollHeight;
  }
}

async function sendCurrentMessage(event) {
  event.preventDefault();
  setSendError("");

  if (!state.peer || !state.userId) {
    return;
  }
  if (!state.peer.is_online) {
    setSendError("对方已离线，消息未发送");
    return;
  }

  const content = dom.messageInput.value.trim();
  if (!content) {
    setSendError("消息不能为空");
    return;
  }
  if (content.length > 1000) {
    setSendError("消息不能超过 1000 个字符");
    return;
  }

  state.sending = true;
  updateComposer();

  try {
    const payload = await api("/api/messages", {
      method: "POST",
      body: {
        sender_id: state.userId,
        receiver_id: state.peer.id,
        content,
      },
    });
    appendMessages([payload.message]);
    dom.messageInput.value = "";
  } catch (error) {
    if (error.status === 409) {
      state.peer.is_online = false;
      updateChatHeader();
    }
    setSendError(error.message);
  } finally {
    state.sending = false;
    updateComposer();
  }
}

function handleNicknameSubmit(event) {
  event.preventDefault();
  const nickname = dom.nicknameInput.value.trim();
  if (!nickname) {
    setLoginStatus("请输入昵称", "error");
    return;
  }

  dom.loginButton.disabled = true;
  setLoginStatus("正在进入聊天室...");
  startSession(null, nickname).catch((error) => {
    dom.loginButton.disabled = false;
    setLoginStatus(error.message, "error");
  });
}

function logout() {
  clearTimers();
  clearSessionStorage();
  state.userId = null;
  state.nickname = null;
  state.peer = null;
  state.onlineUsers = [];
  state.conversations = [];
  state.messageIds.clear();
  state.lastMessageId = 0;
  state.conversationVersion += 1;
  dom.nicknameInput.value = "";
  dom.messageList.replaceChildren();
  dom.onlineList.replaceChildren();
  dom.conversationList.replaceChildren();
  dom.emptyConversation.hidden = false;
  dom.conversationView.hidden = true;
  setLoginStatus("");
  showLoginView();
}

function bindEvents() {
  dom.loginForm.addEventListener("submit", handleNicknameSubmit);
  dom.logoutButton.addEventListener("click", logout);
  dom.messageForm.addEventListener("submit", sendCurrentMessage);
  dom.mobileBack.addEventListener("click", () => {
    document.body.classList.remove("chat-open");
  });
  dom.messageInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!dom.sendButton.disabled) {
        dom.messageForm.requestSubmit();
      }
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && state.userId) {
      scheduleHeartbeat(0);
      if (state.peer) {
        scheduleMessagePoll(0);
      }
    }
  });

  window.addEventListener("online", () => {
    if (state.userId) {
      scheduleHeartbeat(0);
      if (state.peer) {
        scheduleMessagePoll(0);
      }
    }
  });
}

function initializePage() {
  bindEvents();

  if (!state.userId || !state.nickname) {
    showLoginView();
    return;
  }

  dom.nicknameInput.value = state.nickname;
  dom.loginButton.disabled = true;
  setLoginStatus("正在恢复上次会话...");
  startSession(state.userId, state.nickname).catch((error) => {
    dom.loginButton.disabled = false;
    setLoginStatus(
      `恢复会话失败：${error.message}。可以重新输入昵称进入。`,
      "error"
    );
  });
}

initializePage();
