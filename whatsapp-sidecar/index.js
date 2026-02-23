'use strict';

const { Client, LocalAuth } = require('whatsapp-web.js');
const express = require('express');
const qrcode = require('qrcode');
const axios = require('axios');
const fs = require('fs');
const path = require('path');

// Remove stale Chromium lock files left by previous container instances.
// Without this, restarting the container causes "profile is in use" errors.
(function cleanChromiumLocks() {
  const authDir = '.wwebjs_auth';
  if (!fs.existsSync(authDir)) return;
  const lockFiles = ['SingletonLock', 'SingletonSocket', 'SingletonCookie'];
  for (const entry of fs.readdirSync(authDir)) {
    const sessionDir = path.join(authDir, entry);
    for (const lock of lockFiles) {
      const p = path.join(sessionDir, lock);
      try { fs.unlinkSync(p); console.log(`[WhatsApp] Removed stale lock: ${p}`); } catch (_) {}
    }
  }
})();

const PORT = process.env.PORT || 3000;
const REMNANT_URL = process.env.REMNANT_URL || 'http://localhost:8000';

const app = express();
app.use(express.json());

// --- State ---
let qrImageData = null;   // base64 PNG of latest QR code
let clientReady = false;
let lastQrTimestamp = null;

// --- WhatsApp client ---
const client = new Client({
  authStrategy: new LocalAuth({ dataPath: '.wwebjs_auth' }),
  puppeteer: {
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-accelerated-2d-canvas',
      '--no-first-run',
      '--no-zygote',
      '--single-process',
      '--disable-gpu',
    ],
    executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium',
  },
});

client.on('qr', async (qr) => {
  console.log('[WhatsApp] QR code generated — scan with WhatsApp mobile');
  qrImageData = await qrcode.toDataURL(qr);
  lastQrTimestamp = Date.now();
  clientReady = false;
});

client.on('ready', () => {
  console.log('[WhatsApp] Client ready');
  clientReady = true;
  qrImageData = null;
});

client.on('disconnected', (reason) => {
  console.log('[WhatsApp] Disconnected:', reason);
  clientReady = false;
});

client.on('message', async (msg) => {
  if (msg.fromMe) return;

  const payload = {
    from: msg.from,           // e.g. "49123456789@c.us"
    body: msg.body,
    timestamp: msg.timestamp,
    type: msg.type,
    media: msg.hasMedia ? true : false,
  };

  console.log('[WhatsApp] Received message from', msg.from);

  try {
    await axios.post(`${REMNANT_URL}/internal/whatsapp`, payload, {
      timeout: 10000,
    });
  } catch (err) {
    console.error('[WhatsApp] Failed to forward to Remnant:', err.message);
  }
});

client.initialize().catch((err) => {
  console.error('[WhatsApp] Initialization error:', err);
});

// --- REST API ---

// GET /health
app.get('/health', (req, res) => {
  res.json({ status: 'ok', ready: clientReady });
});

// GET /qr — returns base64 PNG image of QR code
app.get('/qr', (req, res) => {
  if (clientReady) {
    return res.status(200).json({ status: 'authenticated', qr: null });
  }
  if (!qrImageData) {
    return res.status(404).json({ error: 'No QR code available yet. Wait a moment.' });
  }
  res.json({
    status: 'pending',
    qr: qrImageData,
    timestamp: lastQrTimestamp,
  });
});

// POST /send — send a WhatsApp message
// Body: { phone: "491234567890@c.us" | "491234567890@lid" | "491234567890", message: "Hello!" }
app.post('/send', async (req, res) => {
  if (!clientReady) {
    return res.status(503).json({ error: 'WhatsApp client not ready. Scan QR first.' });
  }

  const { phone, message } = req.body;
  if (!phone || !message) {
    return res.status(400).json({ error: 'phone and message are required' });
  }

  // Accept full chat IDs (e.g. "351912345678@lid" or "351912345678@c.us")
  // or plain phone numbers (appended with @c.us for backwards compatibility).
  const chatId = phone.includes('@') ? phone : phone.replace(/\D/g, '') + '@c.us';

  try {
    await client.sendMessage(chatId, message);
    console.log('[WhatsApp] Sent message to', chatId);
    res.json({ status: 'sent', to: chatId });
  } catch (err) {
    console.error('[WhatsApp] Send error:', err.message, err.stack);
    res.status(500).json({ error: err.message });
  }
});

// POST /logout — log out and clear auth
app.post('/logout', async (req, res) => {
  try {
    await client.logout();
    clientReady = false;
    qrImageData = null;
    res.json({ status: 'logged_out' });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.listen(PORT, () => {
  console.log(`[WhatsApp Sidecar] Listening on port ${PORT}`);
  console.log(`[WhatsApp Sidecar] Forwarding to Remnant: ${REMNANT_URL}`);
});
