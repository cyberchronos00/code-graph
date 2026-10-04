export function slug(s) {
  return s.trim().toLowerCase().replace(/\s+/g, "-");
}
