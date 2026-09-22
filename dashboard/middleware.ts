import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

// Everything under /dashboard requires a signed-in user. The API
// proxy routes (/api/*) also require auth — they hold the real
// backend key server-side and must not be reachable by anyone who
// isn't logged in.
const isProtectedRoute = createRouteMatcher(["/dashboard(.*)", "/api(.*)"]);

export default clerkMiddleware(async (auth, req) => {
  if (isProtectedRoute(req)) {
    await auth.protect();
  }
});

export const config = {
  matcher: [
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};
