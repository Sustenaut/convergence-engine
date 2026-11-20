// Vite's ?raw import: a file's text, as the bundle scenarios read the built run.js.
declare module "*?raw" {
  const text: string;
  export default text;
}
