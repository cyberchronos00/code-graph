package com.evapp;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name = "Geo")
public class GeoPlugin extends Plugin {
    @PluginMethod
    public void start(PluginCall call) {
        onLocation();
        call.resolve();
    }

    private void onLocation() {
        JSObject d = new JSObject();
        notifyListeners("locationChanged", d);
    }
}
