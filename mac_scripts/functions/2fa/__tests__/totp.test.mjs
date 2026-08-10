import assert from "node:assert/strict";
import test from "node:test";
import { decodeBase32, generateTotp, normalizeBase32 } from "../totp.mjs";

// RFC 6238 Appendix B secret (ASCII) as Base32:
const RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ";

test("normalizeBase32 strips whitespace and uppercases", () => {
  assert.equal(normalizeBase32(" ge zd \n"), "GEZD");
});

test("decodeBase32 decodes RFC secret to ASCII key", () => {
  assert.equal(decodeBase32(RFC_SECRET).toString("ascii"), "12345678901234567890");
});

test("decodeBase32 rejects invalid characters", () => {
  assert.throws(() => decodeBase32("AAAA1AAA"), /invalid/i);
});

test("decodeBase32 rejects empty", () => {
  assert.throws(() => decodeBase32(""), /empty/i);
});

test("generateTotp matches RFC 6238 SHA-1 vectors truncated to 6 digits", () => {
  // 8-digit RFC values → last 6 digits for digits=6
  const cases = [
    { now: 59, otp: "287082" },
    { now: 1111111109, otp: "081804" },
    { now: 1111111111, otp: "050471" },
    { now: 1234567890, otp: "005924" },
    { now: 2000000000, otp: "279037" },
    { now: 20000000000, otp: "353130" },
  ];
  for (const { now, otp } of cases) {
    assert.equal(generateTotp(RFC_SECRET, { now }), otp, `now=${now}`);
  }
});

test("generateTotp accepts pre-decoded key buffer", () => {
  const key = Buffer.from("12345678901234567890", "ascii");
  assert.equal(generateTotp(key, { now: 59 }), "287082");
});

test("generateTotp pre-hashes keys longer than 64 bytes with SHA-1", () => {
  const longKey = Buffer.alloc(65, 1);
  assert.equal(generateTotp(longKey, { now: 59 }), "135355");
});
