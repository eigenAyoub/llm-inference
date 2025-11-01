const form  = document.getElementById('msg-form');
const jobsU = document.getElementById('jobs');
const out   = document.getElementById('out');

const slots = new Map();

// SSE stuff; create once, 
// then wire listeners when ready
window.esReady = (async function () {
  var KEY = 'stream_id', id = sessionStorage.getItem(KEY);
  if (!id) {
    var r = await fetch('/streams/new', { method: 'POST' });
    var d = await r.json();
    if (!d || !d.stream_id) throw new Error('missing stream_id');
    id = d.stream_id; sessionStorage.setItem(KEY, id);
  }
  return new EventSource('/events?stream_id=' + encodeURIComponent(id));
})();

window.esReady.then(function (es) {
  es.onopen = () => console.log('SSE connected');
  es.onerror = (e) => console.log('SSE error (browser will retry)', e);
  es.onmessage = (e) => {
    console.log('DEFAULT message: ', e.data);
    if (typeof out !== 'undefined' && out) out.textContent += e.data;
  };

  es.addEventListener('token', (e) => {
    let msg; try { msg = JSON.parse(e.data); } catch { return; }
    console.log('token >>', msg.token, 'lastEventId', e.lastEventId);
    const span = slots.get(msg.job_id);
    if (span) span.textContent += msg.token;
  });

  es.addEventListener('job_complete', (e) => {
    let msg; try { msg = JSON.parse(e.data); } catch { return; }
    console.log('job_complete', msg.job_id, 'lastEventId', e.lastEventId);
    const li = document.querySelector(`li[data-job-id="${msg.job_id}"]`);
    if (li) li.classList.add('done');
  });
}).catch(err => console.error('SSE init failed:', err));


form.addEventListener('submit', async (e) => {

  e.preventDefault();
  const payload = Object.fromEntries(new FormData(e.currentTarget));
  payload.stream_id = sessionStorage.getItem("stream_id");
  console.log('payload object:', payload);
  console.log('json body:', JSON.stringify(payload));

  let res;
  try {
    res = await fetch(`/submit_job`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch (err) {
    console.log('network error: ' + err);
    return;
  }

  const text = await res.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    data = {};
  }

  if (!res.ok) {
    console.log(`HTTP ${res.status}: ${text}`);
    return;
  }

  const jobId = data['received'];
  if (!jobId) {
    console.log('no job_id in response: ' + text);
    return;
  }

  const li = document.createElement('li');
  li.dataset.jobId = jobId;

  const strong = document.createElement('strong');
  strong.textContent = jobId + ': ';

  const span = document.createElement('span');
  span.className = 'job-output';

  li.append(strong, span);
  jobsU.appendChild(li); 

  slots.set(jobId, span);

  e.currentTarget.reset();  
});
