import { NativeModules } from 'react-native';

const { CalendarModule } = NativeModules;

export function addEvent(name: string, location: string) {
  CalendarModule.createEvent(name, location);
}

export function removeEvent(id: string) {
  NativeModules.CalendarModule.deleteEvent(id);
}
