const SUSPEND_KEEPALIVE_DELAY = 2_147_483_647;

export function createSelectorState(items) {
  return { items: items.map((item) => ({ ...item })), cursor: 0, selected: new Set() };
}

export function decodeKeys(buffer) {
  const input = buffer.toString("utf8");
  const keys = [];
  for (let index = 0; index < input.length;) {
    const sequence = input.slice(index, index + 3);
    if (sequence === "\u001b[A") {
      keys.push("up");
      index += 3;
    } else if (sequence === "\u001b[B") {
      keys.push("down");
      index += 3;
    } else {
      const value = input[index++];
      if (value === "k") keys.push("up");
      else if (value === "j") keys.push("down");
      else if (value === " ") keys.push("toggle");
      else if (value === "\r" || value === "\n") keys.push("submit");
      else if (value === "q" || value === "\u0003") keys.push("cancel");
      else if (value === "\u001a") keys.push("suspend");
    }
  }
  return keys;
}

export function createKeyDecoder() {
  let pending = "";
  return {
    push(buffer) {
      pending += buffer.toString("utf8");
      const hold = pending.endsWith("\u001b") || pending.endsWith("\u001b[");
      const complete = hold ? pending.slice(0, pending.lastIndexOf("\u001b")) : pending;
      pending = hold ? pending.slice(pending.lastIndexOf("\u001b")) : "";
      return decodeKeys(Buffer.from(complete));
    },
  };
}

function childIndicesForSource(items, sourceItem) {
  const values = new Set(sourceItem.childValues ?? []);
  return items.flatMap((item, index) => (
    item.kind === "skill" && values.has(item.value) ? [index] : []
  ));
}

function isSourceGroupSelected(state, sourceIndex) {
  const sourceItem = state.items[sourceIndex];
  if (sourceItem?.kind !== "source") return false;
  const childIndices = childIndicesForSource(state.items, sourceItem);
  if (childIndices.length === 0) return false;
  return childIndices.every((index) => state.selected.has(index));
}

function syncSourceSelection(state, sourceIndex) {
  const sourceItem = state.items[sourceIndex];
  if (sourceItem?.kind !== "source") return;
  if (isSourceGroupSelected(state, sourceIndex)) state.selected.add(sourceIndex);
  else state.selected.delete(sourceIndex);
}

function syncSourceForSkill(state, skillItem) {
  const sourceIndex = state.items.findIndex((item) => (
    item.kind === "source"
    && item.sourceIndex === skillItem.sourceIndex
    && item.childValues?.includes(skillItem.value)
  ));
  if (sourceIndex >= 0) syncSourceSelection(state, sourceIndex);
}

export function reduceSelector(state, key, { multiple }) {
  const next = {
    items: state.items,
    cursor: state.cursor,
    selected: new Set(state.selected),
  };
  if (key === "up") next.cursor = Math.max(0, next.cursor - 1);
  if (key === "down") next.cursor = Math.min(next.items.length - 1, next.cursor + 1);
  if (key === "toggle" && multiple) {
    const item = next.items[next.cursor];
    if (item?.kind === "source" && item.childValues?.length) {
      const childIndices = childIndicesForSource(next.items, item);
      if (isSourceGroupSelected(next, next.cursor)) {
        next.selected.delete(next.cursor);
        for (const index of childIndices) next.selected.delete(index);
      } else {
        next.selected.add(next.cursor);
        for (const index of childIndices) next.selected.add(index);
      }
    } else {
      if (next.selected.has(next.cursor)) next.selected.delete(next.cursor);
      else next.selected.add(next.cursor);
      if (item?.kind === "skill") syncSourceForSkill(next, item);
    }
  }
  if (key === "cancel") return { type: "cancel", state: next, selected: [] };
  if (key === "submit") {
    const indexes = multiple ? [...next.selected].sort((a, b) => a - b) : [next.cursor];
    return { type: "submit", state: next, selected: indexes.map((index) => next.items[index].value) };
  }
  return { type: "continue", state: next, selected: [] };
}

export function runSelector({
  items,
  initial = [],
  multiple,
  input = process.stdin,
  render,
  processRef = process,
  setIntervalImpl = setInterval,
  clearIntervalImpl = clearInterval,
}) {
  return new Promise((resolve, reject) => {
    let state = createSelectorState(items);
    const initialValues = new Set(initial);
    state.selected = new Set(state.items.flatMap((item, index) => (
      initialValues.has(item.value) ? [index] : []
    )));
    let active = true;
    let suspended = false;
    let suspensionKeepalive;
    const decoder = createKeyDecoder();
    const priorRaw = Boolean(input.isRaw);

    const setInputMode = (enabled) => {
      if (input.isTTY && typeof input.setRawMode === "function") input.setRawMode(enabled ? true : priorRaw);
      if (enabled) input.resume();
      else input.pause();
    };

    const removeListeners = () => {
      input.off("data", onData);
      input.off("end", onEnd);
      for (const signal of ["SIGINT", "SIGTERM", "SIGHUP", "SIGTSTP", "SIGCONT"]) {
        processRef.off(signal, signalHandlers[signal]);
      }
    };

    const clearSuspensionKeepalive = () => {
      if (suspensionKeepalive === undefined) return;
      clearIntervalImpl(suspensionKeepalive);
      suspensionKeepalive = undefined;
    };

    const cleanup = () => {
      if (!active) return;
      active = false;
      suspended = false;
      clearSuspensionKeepalive();
      removeListeners();
      setInputMode(false);
    };

    const finish = (result) => {
      cleanup();
      resolve(result);
    };

    const onData = (chunk) => {
      try {
        for (const key of decoder.push(chunk)) {
          if (key === "suspend") {
            suspend();
            return;
          }
          const result = reduceSelector(state, key, { multiple });
          state = result.state;
          if (result.type !== "continue") {
            finish(result);
            return;
          }
          render(state);
        }
      } catch (error) {
        cleanup();
        reject(error);
      }
    };
    const onEnd = () => finish({ type: "cancel", state, selected: [] });
    const cancel = () => finish({ type: "cancel", state, selected: [] });
    const terminate = (signal) => {
      cleanup();
      processRef.kill(processRef.pid, signal);
    };
    const suspend = () => {
      if (!active || suspended) return;
      try {
        suspended = true;
        input.off("data", onData);
        setInputMode(false);
        processRef.off("SIGTSTP", signalHandlers.SIGTSTP);
        suspensionKeepalive = setIntervalImpl(() => {}, SUSPEND_KEEPALIVE_DELAY);
        processRef.kill(processRef.pid, "SIGTSTP");
        processRef.on("SIGTSTP", signalHandlers.SIGTSTP);
      } catch (error) {
        cleanup();
        reject(error);
      }
    };
    const resume = () => {
      if (!active || !suspended) return;
      try {
        suspended = false;
        input.on("data", onData);
        setInputMode(true);
        render(state);
        clearSuspensionKeepalive();
      } catch (error) {
        cleanup();
        reject(error);
      }
    };
    const signalHandlers = {
      SIGINT: cancel,
      SIGTERM: () => terminate("SIGTERM"),
      SIGHUP: () => terminate("SIGHUP"),
      SIGTSTP: suspend,
      SIGCONT: resume,
    };

    input.on("data", onData);
    input.on("end", onEnd);
    for (const [signal, handler] of Object.entries(signalHandlers)) processRef.on(signal, handler);
    try {
      setInputMode(true);
      render(state);
    } catch (error) {
      cleanup();
      reject(error);
    }
  });
}
