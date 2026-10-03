#import <React/RCTEventEmitter.h>

@interface Downloader : RCTEventEmitter <RCTBridgeModule>
@end

@implementation Downloader

RCT_EXPORT_MODULE();

- (NSArray<NSString *> *)supportedEvents
{
  return @[@"downloadProgress", @"downloadDone"];
}

RCT_EXPORT_METHOD(start:(NSString *)url)
{
  [self sendEventWithName:@"downloadProgress" body:@{}];
}

@end
