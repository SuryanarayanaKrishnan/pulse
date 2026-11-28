# Gmail statement import: free setup (about 10 minutes, once)

The code is finished and deployed. It stays switched off until a Google "OAuth client" exists and its two values are saved as a GitHub secret. Nothing here needs a billing account or a card.

What you get: Pulse reads statement emails (read-only) from the Gmail accounts you connect. The phone then downloads each statement and reads it on the device.

## 1. Create the Google project (Google Cloud console, signed in as the Gmail owner)

1. Go to https://console.cloud.google.com and create a project named `Pulse`. If asked to add billing, skip it: nothing here needs it.
2. APIs & Services > Library > search "Gmail API" > Enable.
3. APIs & Services > OAuth consent screen (may be called "Google Auth Platform"):
   - User type: External. App name `Pulse`. Support email and developer email: your own.
   - Scopes: add `https://www.googleapis.com/auth/gmail.readonly`.
   - Audience > Test users: add every Gmail address that will connect (up to 100). Only these can connect while the app is in Testing.
4. APIs & Services > Credentials > Create credentials > OAuth client ID:
   - Application type: Web application. Name `Pulse backend`.
   - Authorised redirect URI, exactly (no trailing slash):
     `https://129-151-139-52.sslip.io/v1/email-connections/gmail/callback`
   - Create, then copy the Client ID and Client secret.

## 2. Give the two values to the backend

GitHub > the `pulse` repository > Settings > Secrets and variables > Actions > the secret `PULSE_ENV` (edit it, or create it). Add two lines, keeping whatever is already there:

```
GOOGLE_CLIENT_ID=<the client id>
GOOGLE_CLIENT_SECRET=<the client secret>
```

Then Actions > "Deploy to Oracle" > Run workflow. The redirect address is filled in by the deploy itself.

## 3. Connect

In the app: Settings > Connections > Gmail switch. Google shows "Google hasn't verified this app": choose Advanced > Go to Pulse (unsafe). That warning is normal for a personal project and only appears for you and your test users. Allow "Read your email". A page says "Gmail connected"; go back to the app.

## Things to know

- **Testing mode expires the link every 7 days.** Google makes the connection of a Testing app lapse weekly; the app then shows "Sign in again to keep reading", one tap. To stop this, on the consent screen set Publishing status to "In production" (still unverified, still free, still capped at 100 users, same warning screen). Verification by Google is only needed beyond that, and it is a paid security assessment for this permission.
- The secret never goes in chat or in the repository; only into `PULSE_ENV`.
- If the address of the server changes (it is built from its IP), the redirect URI above must be updated to match.
