import Network

func startControl() throws -> NWListener {
    let listener = try NWListener(using: .tcp, on: 4040)
    return listener
}

func pingCoap() -> NWConnection {
    return NWConnection(host: "127.0.0.1", port: 5683, using: .udp)
}

func browsePrinters() -> NWBrowser {
    return NWBrowser(for: .bonjour(type: "_printer._tcp", domain: nil), using: .tcp)
}

func advertiseWeb(_ listener: NWListener) {
    listener.service = NWListener.Service(name: "web", type: "_http._tcp")
}
