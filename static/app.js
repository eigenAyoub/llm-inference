const form  = document.getElementById('msg-form');
const jobsU = document.getElementById('jobs');
const out   = document.getElementById('out');

// Routing table: job_id -> span element where tokens will be appended

const slots = new Map();

var uid = Math.random().toString(16).slice(2)
var stream_id = 0

console.log(`User ID is: ${uid}`) ;
console.log(`Stream ID is: ${stream_id}`) ;

const es = new EventSource(`/events`);

es.onopen = ()     => console.log('SSE connected');
es.onerror = ()    => console.log('SSE error (browser will retry)');
es.onmessage = (e) => {
  console.log('DEFAULT message: ', e.data);
  out.textContent += e.data;
}

// Expect: event: token   data: {"job_id":"...", "token":"..."}
es.addEventListener('token', (e) => {
  let msg; try { msg = JSON.parse(e.data); } catch { return; }
  console.log("here >>  ",msg.job_id)
  console.log("lasteventId >>  ",e.lastEventId)
  const span = slots.get(msg.job_id);
  if (span) span.textContent += " " + msg.token;
});

es.addEventListener('stream_id', (e) => {
  let msg; try { msg = JSON.parse(e.data); } catch { return; }
  stream_id = msg.stream_id
  console.log(`Stream ID after is: ${stream_id}`) ;
});


// Expect: event: job_complete   data: {"job_id":"..."}
es.addEventListener('job_complete', (e) => {
  let msg; try { msg = JSON.parse(e.data); } catch { return; }
  const li = document.querySelector(`li[data-job-id="${msg.job_id}"]`);
  if (li) li.classList.add('done');
});

// Submit: POST/submit_job
// add bullet line for returned job_id

form.addEventListener('submit', async (e) => {
  e.preventDefault();

  const payload = Object.fromEntries(new FormData(e.currentTarget));
  payload.stream_id = stream_id
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

  // create: <li data-job-id="..."><strong>uuid:</strong> <span class="job-output"></span></li>
  const li = document.createElement('li');
  li.dataset.jobId = jobId;

  const strong = document.createElement('strong');
  strong.textContent = jobId + ': ';

  const span = document.createElement('span');
  span.className = 'job-output';

  li.append(strong, span);
  jobsU.appendChild(li); 

  // route future tokens for this job_id to this span
  slots.set(jobId, span);

  // e.currentTarget.reset();
});
