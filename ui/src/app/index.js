import { createAppModel } from "./model.js";

let registered = false;

function register(Alpine) {
  if (!Alpine || registered) return;
  registered = true;
  Alpine.data("appShell", createAppModel);
}

if (window.Alpine) register(window.Alpine);

document.addEventListener(
  "alpine:init",
  () => {
    register(window.Alpine);
  },
  { once: true }
);
