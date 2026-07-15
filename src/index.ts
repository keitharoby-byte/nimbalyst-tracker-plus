import { TrackerTimeline } from './timeline/TrackerTimeline';
import './timeline/styles.css';

export const components = { TrackerTimeline };

export function activate(): void {
  console.log('Tracker+ extension activated');
}

export function deactivate(): void {
  console.log('Tracker+ extension deactivated');
}
