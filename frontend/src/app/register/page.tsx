import { AuthForm } from "@/components/features/AuthForm";
import { safeNext } from "@/lib/auth";

export default async function Page({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  return <AuthForm mode="register" next={safeNext(next)} />;
}
