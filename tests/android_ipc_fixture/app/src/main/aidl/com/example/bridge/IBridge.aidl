package com.example.bridge;

import com.example.bridge.IBridgeCallback;

// The bridge between the app and the client library.
interface IBridge {
    String ping();
    /* registers a callback */
    void register(IBridgeCallback callback);
    oneway void sync(in String reason);
}
