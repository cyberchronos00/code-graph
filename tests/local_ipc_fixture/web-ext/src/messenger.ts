import { MessageType } from './messages'

declare const chrome: any

export class Messenger {
  static init() {
    chrome.runtime.onMessage.addListener(Messenger.listener)
  }

  private static listener(message: any) {
    Messenger.onUiMessage(message)
  }

  private static onUiMessage({ type }: any) {
    switch (type) {
      case MessageType.SAVE_ALL:
        break
      case MessageType.RESET:
        break
    }
  }
}
