export default function handler(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ detail: 'Method not allowed.' });
  }

  return res.status(503).json({
    code: 'PERSISTENT_BROWSER_REQUIRED',
    detail: 'Live Protean registration is unavailable on this deployment. The Playwright browser session needs a persistent browser worker, which is not configured. No registration was started or submitted.',
  });
}
