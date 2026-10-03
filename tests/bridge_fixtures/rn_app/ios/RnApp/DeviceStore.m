#import <React/RCTBridgeModule.h>

@interface RCT_EXTERN_MODULE(DeviceStore, NSObject)

RCT_EXTERN_METHOD(getItem:(NSString *)key)

@end
