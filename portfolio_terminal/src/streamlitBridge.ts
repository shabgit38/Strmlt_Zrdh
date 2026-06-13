type StreamlitRenderArgs = {
  snapshot?: unknown;
};

const STREAMLIT_READY = "streamlit:componentReady";
const STREAMLIT_RENDER = "streamlit:render";
const STREAMLIT_FRAME_HEIGHT = "streamlit:setFrameHeight";

function postStreamlitMessage(type: string, data: Record<string, unknown> = {}) {
  window.parent.postMessage(
    {
      isStreamlitMessage: true,
      type,
      ...data,
    },
    "*",
  );
}

export function isStreamlitComponent() {
  return window.parent !== window;
}

export function setStreamlitFrameHeight(height?: number) {
  const nextHeight = height ?? document.documentElement.scrollHeight;
  postStreamlitMessage(STREAMLIT_FRAME_HEIGHT, { height: nextHeight });
}

export function subscribeToStreamlitRender(callback: (args: StreamlitRenderArgs) => void) {
  window.addEventListener("message", (event) => {
    if (event.data?.type !== STREAMLIT_RENDER) {
      return;
    }
    callback(event.data.args ?? {});
  });
  postStreamlitMessage(STREAMLIT_READY, { apiVersion: 1 });
}
