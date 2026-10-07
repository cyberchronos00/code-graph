export function announceInTest(frame: HTMLIFrameElement) {
  frame.contentWindow.postMessage({ type: 'cart', items: 3 }, '*')
}
