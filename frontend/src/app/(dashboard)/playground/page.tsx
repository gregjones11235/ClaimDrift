import { redirect } from "next/navigation";

// /playground is a section header in the sidebar (expand/collapse only — it does
// not navigate). But if someone hits the bare URL directly, send them to the
// first experiment so they don't see a 404.
export default function PlaygroundIndex() {
  redirect("/playground/memory-ab");
}
