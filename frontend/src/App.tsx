import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AppShell } from "@/components/shell/AppShell";
import { Button, EmptyState, Skeleton } from "@/components/ui";
import { ChatGPTWelcome } from "@/components/ai/ChatGPTBits";
import SearchPage from "@/pages/SearchPage";

const LibraryPage = lazy(() => import("@/pages/LibraryPage"));
const RepoDetailPage = lazy(() => import("@/pages/RepoDetailPage"));
const AskPage = lazy(() => import("@/pages/AskPage"));
const ResearchPage = lazy(() => import("@/pages/ResearchPage"));
const ComparePage = lazy(() => import("@/pages/ComparePage"));
const CollectionsPage = lazy(() => import("@/pages/CollectionsPage"));
const CollectionDetailPage = lazy(() => import("@/pages/CollectionDetailPage"));
const ReportPage = lazy(() => import("@/pages/ReportPage"));
const SyncPage = lazy(() => import("@/pages/SyncPage"));
const SettingsPage = lazy(() => import("@/pages/settings/SettingsPage"));
const OnboardingPage = lazy(() => import("@/pages/OnboardingPage"));

function PageFallback() {
  return (
    <div className="mx-auto w-full max-w-[1100px] px-4 pt-8 md:px-8" aria-busy="true">
      <Skeleton className="h-7 w-52" />
      <Skeleton className="mt-6 h-4 w-2/3" />
      <Skeleton className="mt-3 h-4 w-1/2" />
    </div>
  );
}

function NotFound() {
  const location = useLocation();
  return (
    <div className="mx-auto w-full max-w-[900px] px-4 pt-10 md:px-8">
      <EmptyState
        title="There is nothing at this address"
        action={
          <Button variant="primary" onClick={() => (window.location.href = "/search")}>
            Go to Search
          </Button>
        }
      >
        <span className="mono text-sm">{location.pathname}</span> is not a StarIndex page.
      </EmptyState>
    </div>
  );
}

export default function App() {
  return (
    <Suspense fallback={<PageFallback />}>
      <Routes>
        <Route path="/welcome" element={<OnboardingPage />} />
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/search" replace />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/library" element={<Suspense fallback={<PageFallback />}><LibraryPage /></Suspense>} />
          <Route path="/repo/:id" element={<Suspense fallback={<PageFallback />}><RepoDetailPage /></Suspense>} />
          <Route path="/ask" element={<Suspense fallback={<PageFallback />}><AskPage /></Suspense>} />
          <Route path="/research" element={<Suspense fallback={<PageFallback />}><ResearchPage /></Suspense>} />
          <Route path="/compare" element={<Suspense fallback={<PageFallback />}><ComparePage /></Suspense>} />
          <Route path="/collections" element={<Suspense fallback={<PageFallback />}><CollectionsPage /></Suspense>} />
          <Route path="/collections/:id" element={<Suspense fallback={<PageFallback />}><CollectionDetailPage /></Suspense>} />
          <Route path="/reports/:id" element={<Suspense fallback={<PageFallback />}><ReportPage /></Suspense>} />
          <Route path="/sync" element={<Suspense fallback={<PageFallback />}><SyncPage /></Suspense>} />
          <Route path="/settings/*" element={<Suspense fallback={<PageFallback />}><SettingsPage /></Suspense>} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
      <ChatGPTWelcome />
    </Suspense>
  );
}
