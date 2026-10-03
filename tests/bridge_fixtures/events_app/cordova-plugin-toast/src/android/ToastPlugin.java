package com.acme.toast;

import org.apache.cordova.CallbackContext;
import org.apache.cordova.CordovaPlugin;
import org.json.JSONArray;

public class ToastPlugin extends CordovaPlugin {
    @Override
    public boolean execute(String action, JSONArray args, CallbackContext cb) {
        if ("show".equals(action)) {
            show(args.optString(0), cb);
            return true;
        } else if (action.equals("hide")) {
            cb.success();
            return true;
        }
        return false;
    }

    private void show(String msg, CallbackContext cb) {
        cb.success(msg);
    }
}
