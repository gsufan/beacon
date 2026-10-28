/**
 * Collector for the Beacon study files (Google Apps Script web app).
 *
 * It receives what the wizard sends when a participant presses "Send" and stores each file in a private Drive
 * folder; nothing else is read or kept. Setup is in docs/es/estudio.md ("Recibir los resultados").
 *
 * Script property required (Project settings > Script properties):
 *   FOLDER_ID  id of the Drive folder where the files are stored (it stays private to the owner).
 */

var MAX_BYTES = 2000000;
var MIN_QUESTIONS = 3;
var MAX_QUESTIONS = 200;
var MAX_SUBMISSIONS = 300;     // total stored; past this the collector refuses everything (raise it in the script if needed)
var MAX_PER_MINUTE = 10;       // across all senders: slows down anyone trying to flood the folder

function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var cache = CacheService.getScriptCache();
    var minute = 'n' + Math.floor(Date.now() / 60000);
    var recent = Number(cache.get(minute) || 0);
    if (recent >= MAX_PER_MINUTE) return reply({ ok: false, error: 'demasiados envíos, inténtalo en un minuto' });
    cache.put(minute, String(recent + 1), 120);
    var props = PropertiesService.getScriptProperties();
    var stored = Number(props.getProperty('ENVIOS') || 0);
    if (stored >= MAX_SUBMISSIONS) return reply({ ok: false, error: 'el estudio ya no recibe más envíos' });

    var raw = (e && e.postData && e.postData.contents) || '';
    if (!raw || raw.length > MAX_BYTES) return reply({ ok: false, error: 'tamaño inválido' });
    var body;
    try { body = JSON.parse(raw); } catch (err) { return reply({ ok: false, error: 'no es JSON' }); }

    var result = body.resultado;
    var problem = check(result);
    if (problem) return reply({ ok: false, error: problem });

    var id = result.meta.id;
    var folder = DriveApp.getFolderById(props.getProperty('FOLDER_ID'));
    var stamp = Utilities.formatDate(new Date(), 'UTC', 'yyyyMMdd-HHmmss');
    // A repeated id never overwrites an earlier file: the new one gets the time in its name.
    var name = 'beacon-estudio-resultados-' + id + '.json';
    if (folder.getFilesByName(name).hasNext()) name = 'beacon-estudio-resultados-' + id + '-' + stamp + '.json';
    folder.createFile(name, JSON.stringify(result, null, 2), MimeType.PLAIN_TEXT);

    var identity = body.identidad;
    if (identity && typeof identity === 'object' && identity.id === id) {
      var idName = name.replace('resultados', 'identidad');
      folder.createFile(idName, JSON.stringify(identity, null, 2), MimeType.PLAIN_TEXT);
    }
    props.setProperty('ENVIOS', String(stored + 1));
    return reply({ ok: true, recibo: id });
  } catch (err) {
    return reply({ ok: false, error: 'error interno' });
  } finally {
    lock.releaseLock();
  }
}

// Open the web app address in a browser to check that it is deployed.
function doGet() {
  return reply({ ok: true, servicio: 'beacon-estudio' });
}

function check(result) {
  if (!result || typeof result !== 'object') return 'falta el resultado';
  if (result.esquema !== 1) return 'esquema no soportado';
  if (!result.meta || !/^[0-9a-f]{6}$/.test(String(result.meta.id))) return 'id inválido';
  var consent = (result.consentimientos || []).some(function (c) { return c.tipo === 'participacion' && c.aceptado; });
  if (!consent) return 'sin consentimiento de participación';
  var q = result.preguntas;
  if (!Array.isArray(q) || q.length < MIN_QUESTIONS || q.length > MAX_QUESTIONS) return 'número de preguntas inválido';
  return '';
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
