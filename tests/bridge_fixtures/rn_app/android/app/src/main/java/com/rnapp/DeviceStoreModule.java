package com.rnapp;

import com.facebook.react.bridge.ReactApplicationContext;

public class DeviceStoreModule extends NativeDeviceStoreSpec {
    public static final String NAME = "DeviceStore";

    public DeviceStoreModule(ReactApplicationContext context) {
        super(context);
    }

    @Override
    public String getName() {
        return NAME;
    }

    @Override
    public String getItem(String key) {
        return null;
    }

    @Override
    public void setItem(String key, String value) {
    }
}
