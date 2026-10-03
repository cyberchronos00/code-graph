#import "RCTCalendarModule.h"

@implementation RCTCalendarModule

RCT_EXPORT_MODULE(CalendarModule);

RCT_EXPORT_METHOD(createEvent:(NSString *)name location:(NSString *)location)
{
  NSLog(@"create %@ at %@", name, location);
}

@end
