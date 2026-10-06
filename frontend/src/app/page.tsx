import ClaimDriftLanding from '@/components/landing/ClaimDriftLanding';
import { unstable_rethrow } from 'next/navigation';
import { getStats } from '@/lib/api/server';
import type { DashboardStats } from '@/types/claimdrift';

// Server component: fetch whole-index stats once on the server and hand them to
// the (client) landing page, so the hero/impact numbers reflect real data
// instead of hardcoded demo values — with no client-side loading state. If the
// BFF is unreachable we pass null and the landing page falls back to its static
// placeholders, so the marketing page never hard-fails on a backend hiccup.
export default async function HomePage() {
  let stats: DashboardStats | null = null;
  try {
    stats = await getStats();
  } catch (e) {
    unstable_rethrow(e); // an expired session redirects to /login
    stats = null;
  }
  return <ClaimDriftLanding stats={stats} />;
}
