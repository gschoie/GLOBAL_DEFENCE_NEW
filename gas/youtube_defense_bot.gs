// 방산 유튜브 알림 봇 + 3일 모음 (Google Apps Script 정본)
//
// 트리거 두 개로 돈다.
//   checkNewVideos()      — 새 영상 감지 → 제미나이 요약 → 텔레그램 낱개 발송 (자주)
//   sendThreeDayDigest()  — 3일치 링크를 모아 한 번에 발송 (NotebookLM 소스용)
//
// 낱개 발송 때 링크를 스크립트 속성에 같이 쌓아 두고, 3일마다 그걸 비워 내보낸다.
// 유튜브를 다시 긁지 않으므로 봇에 실제로 나간 것과 100% 일치한다.
//
// 키는 소스에 적지 않는다. 프로젝트 설정 → 스크립트 속성에 아래 세 개를 넣을 것.
//   TELEGRAM_TOKEN / TELEGRAM_CHAT_ID / GEMINI_API_KEY
// 넣고 나서 checkSetup() 을 한 번 돌리면 제대로 들어갔는지 확인된다.

const PROPS = PropertiesService.getScriptProperties();

// 3일 모음 버퍼가 쓰는 속성 키 앞머리. LAST_VIDEO_ 와 섞이지 않는다.
const DIGEST_PREFIX = 'DIGEST_';

// 기록해 둔 영상이 피드에서 사라졌을 때(삭제·비공개) 피드 전체를 쏟아내지 않기 위한 상한.
const MAX_NEW_PER_RUN = 5;

// 구독할 방산 채널 목록 (총 7개 채널)
const WATCH_CHANNELS = [
  { name: '샤를세환', id: 'UCVNAlg66t3JhkzT5JntclLg' },
  { name: 'KKMD', id: 'UCLDV9mI3tOQCrdPUWjogQZA' },
  { name: '까치살모', id: 'UCAhe6Ku_oVhkUTv-VfIus8A' },
  { name: '슈퍼소닉', id: 'UCXK_itQ6_JKltErZW_sQojQ' },
  { name: '밀덕', id: 'UCV-slcYbZrNCowaVd3cQaHQ' },
  { name: 'KFN+', id: 'UCObL9hob3R03QSZU5olJZiQ' },
  { name: 'KFN1', id: 'UCXNMgSZqmfX1_K8Uf4l4sog' }
];


// === 설정 ===

function secret(name) {
  const value = PROPS.getProperty(name);
  if (!value) {
    throw new Error('스크립트 속성 ' + name + ' 이 비어 있습니다. '
      + '프로젝트 설정 → 스크립트 속성에서 넣어 주세요.');
  }
  return value;
}

// 한 번 돌려 설정이 제대로 들어갔는지 확인한다.
function checkSetup() {
  ['TELEGRAM_TOKEN', 'TELEGRAM_CHAT_ID', 'GEMINI_API_KEY'].forEach(function (name) {
    const value = PROPS.getProperty(name);
    Logger.log(name + ': ' + (value ? '✅ 설정됨 (' + value.length + '자)' : '❌ 비어 있음'));
  });
  const buffered = digestKeys().length;
  Logger.log('3일 모음 버퍼: ' + buffered + '건');
}

// 3일 모음 트리거를 건다. 한 번만 실행하면 된다(여러 번 눌러도 중복되지 않는다).
function installDigestTrigger() {
  ScriptApp.getProjectTriggers().forEach(function (trigger) {
    if (trigger.getHandlerFunction() === 'sendThreeDayDigest') {
      ScriptApp.deleteTrigger(trigger);
    }
  });
  ScriptApp.newTrigger('sendThreeDayDigest').timeBased().everyDays(3).atHour(7).create();
  Logger.log('3일마다 오전 7시에 sendThreeDayDigest 가 돌도록 걸었습니다.');
}


// === 메인 실행 함수 (HTML 파싱 및 특수문자 에러 방지 버전) ===

function checkNewVideos() {
  WATCH_CHANNELS.forEach(channel => {
    try {
      // 1. 유튜브 RSS 피드를 통해 최근 영상 목록 가져오기
      const url = `https://www.youtube.com/feeds/videos.xml?channel_id=${channel.id}`;
      const response = UrlFetchApp.fetch(url);
      const xml = XmlService.parse(response.getContentText());
      const root = xml.getRootElement();
      const atom = XmlService.getNamespace('http://www.w3.org/2005/Atom');
      const media = XmlService.getNamespace('media', 'http://search.yahoo.com/mrss/');

      const entries = root.getChildren('entry', atom);
      if (entries.length === 0) return;

      // 이전에 마지막으로 알림을 보냈던 영상 ID 가져오기
      const lastSentKey = `LAST_VIDEO_${channel.id}`;
      let lastSentId = PROPS.getProperty(lastSentKey);

      // 피드 상 가장 최신 영상 ID
      const currentNewestId = entries[0].getChildText('id', atom).replace('yt:video:', '');

      // 저장된 기록이 전혀 없거나, 기록이 현재 최신 ID와 같다면 패스하고 기록 확실히 갱신
      if (!lastSentId) {
        PROPS.setProperty(lastSentKey, currentNewestId);
        Logger.log(`${channel.name} 채널 최초 감지 상태 저장 완료 (다음 새 영상부터 알림)`);
        return;
      }

      if (lastSentId === currentNewestId) {
        Logger.log(`${channel.name} 채널은 새로운 영상이 없습니다.`);
        return;
      }

      // 새로 올라온 영상들을 담을 저장소
      let newVideos = [];
      let matched = false;

      // 2. 피드 목록을 확인하며 새 영상 판별
      for (let i = 0; i < entries.length; i++) {
        const entry = entries[i];
        const videoId = entry.getChildText('id', atom).replace('yt:video:', '');

        // 과거에 이미 보낸 영상을 만나면 루프 중단
        if (videoId === lastSentId) {
          matched = true;
          break;
        }

        // 보낸 적 없는 새 영상이라면 배열에 보관
        newVideos.push(entry);
      }

      // 기록해 둔 영상이 피드에 없다(삭제·비공개·15개 초과 업로드).
      // 그대로 두면 피드 전체가 한꺼번에 나가고 제미나이도 그만큼 호출된다.
      if (!matched && newVideos.length > MAX_NEW_PER_RUN) {
        Logger.log(`${channel.name}: 기록(${lastSentId})이 피드에 없어 `
          + `${newVideos.length}건 중 최신 ${MAX_NEW_PER_RUN}건만 보냅니다.`);
        newVideos = newVideos.slice(0, MAX_NEW_PER_RUN);
      }

      // 3. 새 영상이 존재한다면 한꺼번에 발송 처리
      if (newVideos.length > 0) {
        // 먼저 올라온 영상 순서대로 보이기 위해 뒤집기
        newVideos.reverse();

        newVideos.forEach(entry => {
          const videoId = entry.getChildText('id', atom).replace('yt:video:', '');
          let videoTitle = entry.getChildText('title', atom);
          const videoUrl = entry.getChild('link', atom).getAttribute('href').getValue();

          const mediaGroup = entry.getChild('group', media);
          let description = '';
          if (mediaGroup) {
            description = mediaGroup.getChildText('description', media);
          }

          // 제미나이 AI 요약 생성
          let aiSummary = '';
          if (description.trim().length > 10) {
            aiSummary = askGeminiSummary(videoTitle, description);
          } else {
            aiSummary = '영상 설명이 비어있습니다.';
          }

          // 3일 모음 버퍼에 적립 — 발송 전에 넣어 둔다.
          // 텔레그램 전송이 실패해도 링크는 남아 다음 모음에 실린다.
          bufferForDigest(channel.name, videoId, videoTitle, videoUrl);

          // HTML 특수문자 충돌 방지를 위한 안전치환 (< 와 > 부품 보호)
          const safeTitle = escapeHtml(videoTitle);
          const safeSummary = escapeHtml(aiSummary);

          // 텔레그램 메시지 조립
          const message =
            `📺 <b>새로운 방산 영상 업로드!</b>\n\n` +
            `• <b>채널:</b> ${channel.name}\n` +
            `• <b>제목:</b> <b>${safeTitle}</b>\n\n` +
            `• <b>제미나이 AI 핵심 요약:</b>\n${safeSummary}\n\n` +
            `🔗 <b>바로가기:</b> ${videoUrl}`;

          // 텔레그램 전송
          sendTelegramMessage(message);
          Logger.log(`알림 발송 완료 (${channel.name}): ${videoTitle}`);
        });

        // 4. 발송 완료 후 기록 갱신 및 안전 대기시간 부여
        PROPS.setProperty(lastSentKey, currentNewestId);
        Utilities.sleep(500);
      }

    } catch (error) {
      Logger.log(`에러 발생 (${channel.name}): ${error.toString()}`);
    }
  });
}


// === 3일 모음 — NotebookLM 소스용 ===
//
// 낱개 알림을 보낼 때마다 여기 한 건씩 쌓인다. 속성 하나에 영상 하나씩 넣는 이유는
// 스크립트 속성이 값 하나당 9KB 제한이 있어서다 — JSON 배열 하나로 모으면 언젠가 터진다.

function digestKeys() {
  const all = PROPS.getProperties();
  return Object.keys(all)
    .filter(function (key) { return key.indexOf(DIGEST_PREFIX) === 0; })
    .sort();  // 키 앞머리가 시각이라 정렬하면 올라온 순서가 된다
}

function bufferForDigest(channelName, videoId, title, url) {
  // 같은 밀리초에 두 건이 들어와도 videoId 가 붙어 있어 덮어쓰이지 않는다.
  const key = DIGEST_PREFIX + Date.now() + '_' + videoId;
  PROPS.setProperty(key, JSON.stringify({ ch: channelName, t: title, u: url }));
}

function sendThreeDayDigest() {
  const keys = digestKeys();
  if (keys.length === 0) {
    Logger.log('3일 모음: 쌓인 영상이 없어 보내지 않습니다.');
    return;
  }

  const all = PROPS.getProperties();
  const rows = [];
  keys.forEach(function (key) {
    try {
      rows.push(JSON.parse(all[key]));
    } catch (e) {
      Logger.log('3일 모음: 못 읽는 항목 하나를 건너뜁니다 — ' + key);
    }
  });
  if (rows.length === 0) {
    keys.forEach(function (key) { PROPS.deleteProperty(key); });
    return;
  }

  // 채널 순서는 WATCH_CHANNELS 순서를 따른다
  const order = {};
  WATCH_CHANNELS.forEach(function (channel, index) { order[channel.name] = index; });

  const byChannel = {};
  rows.forEach(function (row) {
    const name = row.ch || '(채널 미상)';
    if (!byChannel[name]) byChannel[name] = [];
    byChannel[name].push(row);
  });
  const names = Object.keys(byChannel).sort(function (a, b) {
    const ra = (a in order) ? order[a] : 99;
    const rb = (b in order) ? order[b] : 99;
    return ra - rb;
  });

  // 1) 읽는 판 — 채널별 제목
  const stamp = Utilities.formatDate(new Date(), 'Asia/Seoul', 'M월 d일');
  let lines = [
    `📻 <b>3일 모음 · ${stamp}</b>`,
    `채널 ${names.length}개 · 영상 ${rows.length}건`,
    ''
  ];
  names.forEach(function (name) {
    lines.push('<b>' + escapeHtml(name) + '</b>');
    byChannel[name].forEach(function (row) {
      lines.push('• ' + escapeHtml(row.t || ''));
    });
    lines.push('');
  });
  lines.push('↓ 아래 메시지를 통째로 복사해 NotebookLM 소스에 붙여넣으세요');
  sendChunked(lines, false);

  // 2) 붙여넣는 판 — 주소만. 다른 글자가 섞이면 복사가 번거로워진다.
  const urls = [];
  names.forEach(function (name) {
    byChannel[name].forEach(function (row) {
      if (row.u) urls.push(row.u);
    });
  });
  sendChunked(urls, true);

  // 보낸 뒤에만 비운다. 위에서 예외가 나면 버퍼가 남아 다음 회차에 다시 실린다.
  keys.forEach(function (key) { PROPS.deleteProperty(key); });
  Logger.log('3일 모음 발송 완료 — 채널 ' + names.length + '개 · 영상 ' + rows.length + '건');
}

// 텔레그램 한 통 4096자 제한 — 줄 단위로 끊어 보낸다.
function sendChunked(lines, plain) {
  let buffer = [];
  let size = 0;
  lines.forEach(function (line) {
    if (buffer.length > 0 && size + line.length + 1 > 3500) {
      sendTelegramMessage(buffer.join('\n'), { plain: plain, noPreview: true });
      Utilities.sleep(1100);
      buffer = [];
      size = 0;
    }
    buffer.push(line);
    size += line.length + 1;
  });
  if (buffer.length > 0) {
    sendTelegramMessage(buffer.join('\n'), { plain: plain, noPreview: true });
  }
}


// === 제미나이 API 호출 및 요약 함수 ===

function askGeminiSummary(title, text) {
  try {
    const url = 'https://generativelanguage.googleapis.com/v1beta/models/'
      + 'gemini-2.5-flash:generateContent?key=' + secret('GEMINI_API_KEY');

    const prompt = `너는 밀리터리, 방위산업 전문 뉴스 요약가야. 유튜브 영상의 제목과 상세 설명을 바탕으로 핵심 내용을 요약해줘.\n\n` +
                   `[영상 제목]: ${title}\n` +
                   `[영상 상세설명]: ${text}\n\n` +
                   `위 내용을 바탕으로 가독성이 좋게 이모지(•)를 사용한 2~3줄의 문장으로 한국어로 요약해줘. 인사말이나 다른 군더더기 말은 다 빼고 오직 요약 내용만 출력해줘.`;

    const payload = {
      "contents": [{
        "parts": [{ "text": prompt }]
      }]
    };

    const options = {
      "method": "post",
      "contentType": "application/json",
      "payload": JSON.stringify(payload),
      "muteHttpExceptions": true
    };

    const response = UrlFetchApp.fetch(url, options);
    const json = JSON.parse(response.getContentText());

    if (json.candidates && json.candidates[0].content && json.candidates[0].content.parts) {
      return json.candidates[0].content.parts[0].text.trim();
    } else {
      return "⚠️ AI 요약 생성 중 오류가 발생했습니다.";
    }
  } catch (e) {
    return "⚠️ 제미나이 API 연결 실패: " + e.toString();
  }
}


// === 텔레그램 메시지 전송 함수 ===
//
// opts.plain    : true 면 HTML 파싱을 끈다 (주소만 보낼 때 — 태그로 오해받을 일이 없다)
// opts.noPreview: true 면 링크 미리보기 카드를 안 만든다 (모음 메시지가 길어지는 걸 막는다)

function sendTelegramMessage(text, opts) {
  opts = opts || {};
  const url = `https://api.telegram.org/bot${secret('TELEGRAM_TOKEN')}/sendMessage`;
  const payload = {
    'chat_id': secret('TELEGRAM_CHAT_ID'),
    'text': text,
    'disable_web_page_preview': opts.noPreview === true
  };
  if (opts.plain !== true) {
    payload['parse_mode'] = 'HTML';
  }

  const options = {
    'method': 'post',
    'contentType': 'application/json',
    'payload': JSON.stringify(payload)
  };

  UrlFetchApp.fetch(url, options);
}


// === HTML 특수문자 무력화 안전 함수 ===

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
