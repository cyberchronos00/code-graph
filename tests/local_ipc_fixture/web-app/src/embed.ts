export function reportHeight(height: number) {
  window.parent.postMessage({ type: 'resize', height }, '*')
}

export function openPicker(frame: HTMLIFrameElement) {
  frame.contentWindow.postMessage({ action: 'pick' }, 'https://picker.example.com')
}
