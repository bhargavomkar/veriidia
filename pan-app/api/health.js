import { neon } from '@neondatabase/serverless';

export default async function handler(req, res) {
  if (req.method !== 'GET') {
    res.setHeader('Allow', 'GET');
    return res.status(405).json({ status: 'error', error: 'Method not allowed.' });
  }

  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) {
    return res.status(503).json({ status: 'error', database: 'not_configured' });
  }

  try {
    const sql = neon(databaseUrl);
    const result = await sql`SELECT 1 AS ok`;
    if (Number(result[0]?.ok) !== 1) throw new Error('Health query did not return the expected result.');
    return res.status(200).json({ status: 'ok', database: 'connected' });
  } catch {
    return res.status(503).json({ status: 'error', database: 'unavailable' });
  }
}
