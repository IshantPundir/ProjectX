export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6 py-12">
      <div className="max-w-md text-center">
        <h1 className="text-2xl font-semibold mb-3">Page not found</h1>
        <p className="text-base opacity-80">
          This URL does not match any interview link. Please check the link in
          your email or contact your recruiter.
        </p>
      </div>
    </main>
  );
}
