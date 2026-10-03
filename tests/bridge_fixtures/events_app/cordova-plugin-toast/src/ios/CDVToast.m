#import "CDVToast.h"

@implementation CDVToast

- (void)show:(CDVInvokedUrlCommand*)command
{
    CDVPluginResult* r = [CDVPluginResult resultWithStatus:CDVCommandStatus_OK];
    [self.commandDelegate sendPluginResult:r callbackId:command.callbackId];
}

- (void)hide:(CDVInvokedUrlCommand*)command
{
}

@end
