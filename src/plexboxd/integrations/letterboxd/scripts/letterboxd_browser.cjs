function parseArgs(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 1) {
    const current = argv[index];
    if (!current.startsWith('--')) continue;
    const key = current.slice(2);
    const next = argv[index + 1];
    if (!next || next.startsWith('--')) {
      parsed[key] = 'true';
      continue;
    }
    parsed[key] = next;
    index += 1;
  }
  return parsed;
}

function looksLikeCloudflare(text) {
  const lowered = (text || '').toLowerCase();
  return (
    (lowered.includes('just a moment') && lowered.includes('cloudflare')) ||
    lowered.includes('cf_chl_') ||
    (lowered.includes('attention required') && lowered.includes('cloudflare')) ||
    (lowered.includes('access denied') && lowered.includes('cloudflare')) ||
    lowered.includes('error1015') ||
    lowered.includes('you are being rate limited')
  );
}

// Note: letterboxd.com serves a static analytics stub containing
// `loggedIn: false` / `role: "guest"` even to authenticated members, so that text is
// useless as a signal. A guest asking for a members-only page gets a 200 carrying the
// sign-in flow body class instead of a redirect, so that is what we look for.
function looksLoggedOut(text) {
  const lowered = (text || '').toLowerCase();
  return lowered.includes('screen-standalone-flow-sign-in') || lowered.includes('id="sign-in-form"');
}

// Payload for POST /api/v0/production-log-entries. productionId must be the base-62
// LID (e.g. "gdKW"); the numeric film id is rejected. Rating is stars as a float.
function buildPayload(args) {
  const payload = {
    productionId: String(args.lid || args['film-id']),
    diaryDetails: {
      diaryDate: args['watched-on'],
      rewatch: args.rewatch === 'true',
    },
    tags: parseTags(args.tags),
    like: args.liked === 'true',
  };
  const rating = Number(args.rating);
  if (Number.isFinite(rating) && rating > 0) {
    payload.rating = rating;
  }
  if (args.review && args.review.trim()) {
    payload.review = { text: args.review.trim(), containsSpoilers: false };
  }
  return payload;
}

function parseTags(raw) {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter((tag) => tag && String(tag).trim()) : [];
  } catch (_error) {
    return [];
  }
}

const fs = require('fs');

function normalizeCookies(rawCookies) {
  if (!Array.isArray(rawCookies)) {
    return [];
  }
  const nowSeconds = Date.now() / 1000;
  return rawCookies
    .filter((cookie) => cookie && cookie.name && cookie.value !== undefined)
    // Never seed an expired cookie: doing so overwrites a still-valid cookie already
    // held by the persistent profile and silently logs the session out.
    .filter((cookie) => !(Number(cookie.expires) > 0 && Number(cookie.expires) <= nowSeconds))
    .map((cookie) => {
      const normalized = {
        name: cookie.name,
        value: String(cookie.value),
        path: cookie.path || '/',
      };
      if (cookie.url) {
        normalized.url = cookie.url;
      } else if (cookie.domain) {
        normalized.domain = cookie.domain;
      } else {
        normalized.domain = 'letterboxd.com';
      }
      if (cookie.expires && Number(cookie.expires) > 0) {
        normalized.expires = Number(cookie.expires);
      }
      if (cookie.httpOnly !== undefined) {
        normalized.httpOnly = Boolean(cookie.httpOnly);
      }
      if (cookie.secure !== undefined) {
        normalized.secure = Boolean(cookie.secure);
      }
      if (cookie.sameSite && ['Strict', 'Lax', 'None'].includes(cookie.sameSite)) {
        normalized.sameSite = cookie.sameSite;
      }
      return normalized;
    });
}

async function seedCookiesFromFile(context, cookieFilePath) {
  if (!cookieFilePath || !fs.existsSync(cookieFilePath)) {
    return;
  }
  const payload = JSON.parse(fs.readFileSync(cookieFilePath, 'utf8'));
  const cookies = normalizeCookies(Array.isArray(payload) ? payload : payload.cookies);
  if (cookies.length > 0) {
    await context.addCookies(cookies);
  }
}

async function getAutomationPackage() {
  const preferredPackage = process.env.LETTERBOXD_BROWSER_PACKAGE || 'patchright';
  try {
    return require(preferredPackage);
  } catch (preferredError) {
    if (preferredPackage !== 'playwright') {
      try {
        return require('playwright');
      } catch (_fallbackError) {
        throw preferredError;
      }
    }
    throw preferredError;
  }
}

async function assertNoChallenge(page) {
  // Reading the page can race an in-flight navigation ("Execution context was
  // destroyed"); settle first and treat an unreadable page as "no challenge seen"
  // rather than turning a transient race into a hard failure.
  await page.waitForLoadState('domcontentloaded').catch(() => {});

  let title = '';
  let html = '';
  try {
    title = await page.title();
    html = await page.content();
  } catch (_error) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    try {
      title = await page.title();
      html = await page.content();
    } catch (_retryError) {
      return;
    }
  }

  if (looksLikeCloudflare(`${title}\n${html}`)) {
    throw new Error('Cloudflare challenge detected in browser flow');
  }
}

async function extractCookies(context) {
  return context.cookies('https://letterboxd.com');
}

async function extractSignedInAs(context) {
  const cookies = await extractCookies(context);
  const signedInCookie = cookies.find((cookie) => cookie.name === 'letterboxd.signed.in.as');
  return signedInCookie ? signedInCookie.value : null;
}

async function isLoggedIn(page, context) {
  const signedInAs = await extractSignedInAs(context);
  if (signedInAs) {
    return true;
  }

  // The server marks authenticated pages with a `logged-in` body class. There is no
  // longer a /sign-out/ href to look for.
  return page.evaluate(() => document.body.classList.contains('logged-in'));
}

async function extractCsrfCookie(context) {
  const cookies = await extractCookies(context);
  const csrfCookie = cookies.find((cookie) => cookie.name === 'com.xk72.webparts.csrf');
  return csrfCookie ? csrfCookie.value : null;
}

async function extractCsrfToken(page, context) {
  const pageToken = await page.evaluate(() => {
    const tokenInput = document.querySelector('input[name="__csrf"]');
    return tokenInput && tokenInput.value ? tokenInput.value : null;
  });
  if (pageToken) {
    return pageToken;
  }
  return extractCsrfCookie(context);
}

// A fresh profile gets a GDPR consent dialog whose overlay swallows every click, so
// it has to be dismissed before the sign-in link can be used.
async function dismissConsentDialog(page) {
  const present = await page
    .locator('.fc-consent-root')
    .count()
    .catch(() => 0);
  if (!present) {
    return;
  }

  for (const selector of ['.fc-cta-consent', '.fc-cta-manage-options', '.fc-button.fc-cta-consent']) {
    const clicked = await page
      .locator(selector)
      .first()
      .click({ timeout: 4000 })
      .then(() => true)
      .catch(() => false);
    if (clicked) {
      await page.waitForTimeout(1200);
      break;
    }
  }

  // If the dialog is still up, remove it so it cannot intercept pointer events.
  await page.evaluate(() => {
    document.querySelectorAll('.fc-consent-root, .fc-dialog-overlay').forEach((node) => node.remove());
  });
  await page.waitForTimeout(300);
}

async function openSignInForm(page) {
  const alreadyVisible = await page
    .locator('#field-username')
    .isVisible()
    .catch(() => false);
  if (alreadyVisible) {
    return;
  }

  await dismissConsentDialog(page);

  // /sign-in/ renders the form inline on the homepage; navigate rather than click so a
  // stray overlay cannot block the flow.
  await page.goto('https://letterboxd.com/sign-in/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1500);
  await dismissConsentDialog(page);

  const visible = await page
    .locator('#field-username')
    .isVisible()
    .catch(() => false);
  if (visible) {
    return;
  }

  await page.evaluate(() => {
    const trigger = [...document.querySelectorAll('a,button')].find((node) =>
      /sign in|log in/i.test(node.textContent || '')
    );
    if (trigger) trigger.click();
  });
  await page.waitForTimeout(1500);
}

async function waitForSignedInCookie(context, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await extractSignedInAs(context)) {
      return true;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return false;
}

async function ensureLoggedIn(page, context, args) {
  await page.goto('https://letterboxd.com/activity/', { waitUntil: 'domcontentloaded' });
  await assertNoChallenge(page);
  if (await isLoggedIn(page, context)) {
    return;
  }

  if (!args.username || !args.password) {
    throw new Error('Letterboxd credentials are required for browser login');
  }

  await page.goto('https://letterboxd.com/', { waitUntil: 'domcontentloaded' });
  await assertNoChallenge(page);
  await openSignInForm(page);

  await page.locator('#field-username').waitFor({ state: 'visible', timeout: 20000 });
  await page.locator('#field-username').fill(args.username);
  const passwordField = page.locator('#field-password');
  await passwordField.fill(args.password);
  await passwordField.press('Enter');

  // Wait for the signed-in cookie rather than a fixed delay: the POST to
  // /user/login.do plus the follow-up redirect can be slow in a container, and a short
  // sleep here made the login look like it had failed when it had not.
  await waitForSignedInCookie(context, 30000);
  await assertNoChallenge(page);

  await page.goto('https://letterboxd.com/activity/', { waitUntil: 'domcontentloaded' });
  await assertNoChallenge(page);
  if (!(await isLoggedIn(page, context))) {
    const html = await page.content();
    const title = await page.title();
    const snippet = html
      .replace(/\s+/g, ' ')
      .slice(0, 400);
    throw new Error(
      `Letterboxd login did not reach an authenticated browser state (url=${page.url()} title=${title} snippet=${snippet})`
    );
  }
}

async function handleVerify(page, context) {
  await page.goto('https://letterboxd.com/activity/', { waitUntil: 'domcontentloaded' });
  await assertNoChallenge(page);
  if (!(await isLoggedIn(page, context))) {
    throw new Error('Persisted browser profile is not logged in');
  }
  return {
    command: 'verify',
    ok: true,
    signedInAs: await extractSignedInAs(context),
    cookies: await extractCookies(context),
  };
}

async function handleBootstrap(page, context, args) {
  await ensureLoggedIn(page, context, args);
  return {
    command: 'bootstrap',
    ok: true,
    signedInAs: await extractSignedInAs(context),
    cookies: await extractCookies(context),
  };
}

async function resolveLid(page, slug) {
  return page.evaluate(async (filmSlug) => {
    const response = await fetch(`/film/${filmSlug}/json/`, { headers: { Accept: 'application/json' } });
    if (!response.ok) return null;
    try {
      const data = await response.json();
      return data && data.lid ? String(data.lid) : null;
    } catch (_error) {
      return null;
    }
  }, slug);
}

async function handleWrite(page, context, args) {
  await ensureLoggedIn(page, context, args);

  // Load the film page first: it sets the referer the API expects and gives us the LID.
  const slug = args.slug;
  await page.goto(`https://letterboxd.com/film/${slug}/`, { waitUntil: 'domcontentloaded' });
  await assertNoChallenge(page);

  if (!args.lid) {
    args.lid = await resolveLid(page, slug);
  }
  if (!args.lid) {
    throw new Error(`Could not resolve Letterboxd LID for film slug '${slug}'`);
  }

  const csrfToken = await extractCsrfToken(page, context);
  if (!csrfToken) {
    throw new Error('Missing CSRF token in browser context');
  }

  const payload = buildPayload(args);
  const result = await page.evaluate(async ({ requestPayload, csrf }) => {
    const response = await fetch('https://letterboxd.com/api/v0/production-log-entries', {
      method: 'POST',
      headers: {
        'Accept': '*/*',
        'Content-Type': 'application/json; charset=UTF-8',
        'X-CSRF-Token': csrf,
      },
      body: JSON.stringify(requestPayload),
      credentials: 'include',
    });
    return {
      ok: response.ok,
      status: response.status,
      url: response.url,
      body: await response.text(),
    };
  }, { requestPayload: payload, csrf: csrfToken });

  let parsedBody = null;
  try {
    parsedBody = JSON.parse(result.body);
  } catch (_error) {
    parsedBody = null;
  }

  return {
    command: 'write',
    result,
    parsedBody,
    signedInAs: await extractSignedInAs(context),
    cookies: await extractCookies(context),
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const automation = await getAutomationPackage();
  const { chromium } = automation;
  const launchOptions = { headless: args.headless === 'true' };
  if (args['browser-channel']) {
    launchOptions.channel = args['browser-channel'];
  } else if (args['executable-path']) {
    launchOptions.executablePath = args['executable-path'];
  }

  const context = await chromium.launchPersistentContext(args['profile-dir'], launchOptions);
  try {
    await seedCookiesFromFile(context, args['cookie-file']);
    const page = context.pages()[0] || (await context.newPage());
    let payload = null;

    if (args.command === 'verify') {
      payload = await handleVerify(page, context);
    } else if (args.command === 'bootstrap') {
      payload = await handleBootstrap(page, context, args);
    } else if (args.command === 'write') {
      payload = await handleWrite(page, context, args);
    } else {
      throw new Error(`Unsupported command: ${args.command}`);
    }

    console.log(JSON.stringify(payload));
  } finally {
    await context.close();
  }
}

main().catch((error) => {
  const message = String(error && error.stack ? error.stack : error);
  console.error(message);
  process.exit(1);
});
