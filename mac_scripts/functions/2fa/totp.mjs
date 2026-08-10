import { createHmac, createHash } from "node:crypto";

const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

export function normalizeBase32(input) {
  return String(input).replace(/\s+/g, "").toUpperCase();
}

export function decodeBase32(raw) {
  const secret = normalizeBase32(raw);
  if (!secret) throw new Error("empty secret");

  if (/[^A-Z2-7=]/.test(secret)) {
    throw new Error("invalid Base32 character");
  }

  let data;
  if (secret.includes("=")) {
    const eqIndex = secret.indexOf("=");
    data = secret.slice(0, eqIndex);
    const padding = secret.slice(eqIndex);

    if (padding !== "=".repeat(padding.length)) {
      throw new Error("padding must appear only at the end");
    }
    if (secret.length % 8 !== 0) {
      throw new Error("padded Base32 length must be a multiple of 8");
    }

    const padLen = padding.length;
    const rem = data.length % 8;
    const validPairs = { "0:0": true, "2:6": true, "4:4": true, "5:3": true, "7:1": true };
    if (!validPairs[`${rem}:${padLen}`]) {
      throw new Error("invalid Base32 padding");
    }
  } else {
    data = secret;
    const rem = data.length % 8;
    if (![0, 2, 4, 5, 7].includes(rem)) {
      throw new Error("invalid unpadded Base32 length");
    }
  }

  if (!data) throw new Error("empty Base32 data");

  const bytes = [];
  let buffer = 0;
  let bits = 0;

  for (const char of data) {
    const value = ALPHABET.indexOf(char);
    if (value < 0) throw new Error("invalid Base32 character");

    buffer = buffer * 32 + value;
    bits += 5;

    while (bits >= 8) {
      bits -= 8;
      const byte = Math.floor(buffer / 2 ** bits);
      bytes.push(byte);
      buffer -= byte * 2 ** bits;
    }
  }

  if (bits > 0 && buffer !== 0) {
    throw new Error("invalid Base32 encoding");
  }

  const result = Buffer.from(bytes);
  if (result.length === 0) throw new Error("decoded secret is empty");

  return result;
}

export function generateTotp(secretOrKey, {
  now = Date.now() / 1000,
  step = 30,
  digits = 6,
} = {}) {
  let key = Buffer.isBuffer(secretOrKey)
    ? secretOrKey
    : decodeBase32(secretOrKey);

  if (key.length > 64) {
    key = createHash("sha1").update(key).digest();
  }

  const counter = Math.floor(now / step);
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(counter));

  const hmac = createHmac("sha1", key).update(msg).digest();
  const offset = hmac[hmac.length - 1] & 0xf;
  const bin =
    ((hmac[offset] & 0x7f) << 24)
    | ((hmac[offset + 1] & 0xff) << 16)
    | ((hmac[offset + 2] & 0xff) << 8)
    | (hmac[offset + 3] & 0xff);
  const otp = bin % (10 ** digits);
  return String(otp).padStart(digits, "0");
}
