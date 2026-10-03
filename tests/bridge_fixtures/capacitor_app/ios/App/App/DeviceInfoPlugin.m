#import <Capacitor/Capacitor.h>

CAP_PLUGIN(DeviceInfoPlugin, "DeviceInfo",
    CAP_PLUGIN_METHOD(getInfo, CAPPluginReturnPromise);
)
