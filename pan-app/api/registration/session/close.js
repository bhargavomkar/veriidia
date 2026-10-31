import { ApiError, closeSessionBeacon } from '../../../lib/registration-service.js';

export default async function handler(req, res) {
  if (req.method !== 'POST') {
    res.setHeader('Allow', 'POST');
    return res.status(405).json({ detail: 'Method not allowed.' });
  }
  try {
    return res.status(200).json(await closeSessionBeacon(req));
  } catch (error) {
    const status = error instanceof ApiError ? error.status : 500;
    return res.status(status).json({ detail: error instanceof ApiError ? error.message : 'Could not close the registration session.' });
  }
}
