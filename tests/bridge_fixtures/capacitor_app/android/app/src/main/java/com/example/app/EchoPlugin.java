package com.example.app;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name = "Echo")
public class EchoPlugin extends Plugin {

    @PluginMethod
    public void echo(PluginCall call) {
        JSObject ret = new JSObject();
        ret.put("value", call.getString("value"));
        call.resolve(ret);
    }

    @PluginMethod(returnType = PluginMethod.RETURN_NONE)
    public void ping(PluginCall call) {
        call.resolve();
    }

    @PluginMethod
    public void vibrate(PluginCall call) {
        call.resolve();
    }

    public void helper() {
    }
}
