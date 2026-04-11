/**
 * Google Apps Script keep-alive for Render-style services.
 *
 * Apps Script does not support a native 14-minute trigger.
 * The workaround is:
 * 1. Run a trigger every minute.
 * 2. Only send the HTTP ping when 14 minutes have passed since the last success.
 */

const KEEP_ALIVE_CONFIG = Object.freeze({
  targetUrl: 'https://time-tracker-y73y.onrender.com/healthz',
  minIntervalMinutes: 14,
  requestTimeoutMs: 20000,
  userAgent: 'GoogleAppsScript-KeepAlive/1.0',
});

function keepAlive() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    console.log('Otro trigger sigue en ejecucion; se omite esta pasada.');
    return;
  }

  try {
    const properties = PropertiesService.getScriptProperties();
    const nowMs = Date.now();
    const lastSuccessMs = Number(properties.getProperty('KEEP_ALIVE_LAST_SUCCESS_MS') || '0');
    const minIntervalMs = KEEP_ALIVE_CONFIG.minIntervalMinutes * 60 * 1000;

    if (lastSuccessMs && nowMs - lastSuccessMs < minIntervalMs) {
      console.log('Aun no han pasado 14 minutos desde el ultimo ping correcto.');
      return;
    }

    const response = UrlFetchApp.fetch(KEEP_ALIVE_CONFIG.targetUrl, {
      method: 'get',
      muteHttpExceptions: true,
      followRedirects: true,
      headers: {
        'Cache-Control': 'no-cache',
        'User-Agent': KEEP_ALIVE_CONFIG.userAgent,
      },
    });

    const statusCode = response.getResponseCode();
    const bodyPreview = response.getContentText().slice(0, 200);
    console.log(`Ping ${statusCode}: ${bodyPreview}`);

    if (statusCode >= 200 && statusCode < 400) {
      properties.setProperty('KEEP_ALIVE_LAST_SUCCESS_MS', String(nowMs));
      return;
    }

    throw new Error(`Ping fallido con codigo ${statusCode}`);
  } catch (error) {
    console.error(`keepAlive error: ${error.message}`);
    throw error;
  } finally {
    lock.releaseLock();
  }
}

function installKeepAliveTrigger() {
  removeKeepAliveTriggers();
  ScriptApp.newTrigger('keepAlive')
    .timeBased()
    .everyMinutes(1)
    .create();

  console.log('Trigger creado. Se ejecuta cada minuto y hace ping cada 14 minutos aprox.');
}

function removeKeepAliveTriggers() {
  ScriptApp.getProjectTriggers().forEach((trigger) => {
    if (trigger.getHandlerFunction() === 'keepAlive') {
      ScriptApp.deleteTrigger(trigger);
    }
  });
}

function resetKeepAliveState() {
  PropertiesService.getScriptProperties().deleteProperty('KEEP_ALIVE_LAST_SUCCESS_MS');
  console.log('Estado reiniciado.');
}

function testKeepAlive() {
  keepAlive();
}
