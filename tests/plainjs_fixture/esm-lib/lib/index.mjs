import { slug } from "./slug.mjs";

export function title(s) {
  return slug(s).toUpperCase();
}
