import {icon} from './card-menu.js?v=3.3.5';

// A card keeps its existing poster. Only the active card owns a video decoder.
export function createCardTurntables(grid) {
  const canHover = matchMedia('(hover: hover) and (pointer: fine)');
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let active = null;
  let hoverTimer = null;
  const observer = new IntersectionObserver(entries => {
    if (entries.some(entry => entry.target === active?.card && !entry.isIntersecting)) stop();
  });

  function clearHover() {
    clearTimeout(hoverTimer);
    hoverTimer = null;
  }

  function stop() {
    clearHover();
    if (!active) return;
    const {card, video, frame, cancelFrame, setButton} = active;
    active = null;
    observer.unobserve(card);
    cancelFrame();
    video.pause();
    video.removeAttribute('src');
    video.load();
    frame.remove();
    card.classList.remove('turntable-playing');
    setButton(false);
  }

  function start(entry, hovered) {
    stop();
    if (!entry.card.isConnected || document.hidden || grid.classList.contains('selecting')) return;
    // A detached decoder keeps browser media overlays off the card preview.
    const frame = document.createElement('canvas');
    frame.className = 'card-turntable-frame';
    frame.setAttribute('aria-hidden', 'true');
    frame.hidden = true;
    const context = frame.getContext('2d');
    if (!context) return;
    const video = document.createElement('video');
    video.muted = true;
    video.playsInline = true;
    video.loop = true;
    video.preload = 'none';
    let frameHandle;
    const nextFrame = video.requestVideoFrameCallback
      ? callback => video.requestVideoFrameCallback(callback)
      : callback => setTimeout(callback, 1000 / 12);
    const cancelFrame = video.cancelVideoFrameCallback
      ? () => video.cancelVideoFrameCallback(frameHandle)
      : () => clearTimeout(frameHandle);
    function paint() {
      if (active?.video !== video) return;
      if (video.readyState >= video.HAVE_CURRENT_DATA && video.videoWidth && video.videoHeight) {
        if (frame.hidden) {
          const scale = Math.min(1, 384 / Math.max(video.videoWidth, video.videoHeight));
          frame.width = Math.max(1, Math.round(video.videoWidth * scale));
          frame.height = Math.max(1, Math.round(video.videoHeight * scale));
          entry.card.classList.add('turntable-playing');
          frame.hidden = false;
        }
        context.drawImage(video, 0, 0, frame.width, frame.height);
      }
      frameHandle = nextFrame(paint);
    }
    active = {...entry, video, frame, cancelFrame, hovered};
    entry.media.append(frame);
    entry.setButton(true);
    observer.observe(entry.card);
    const failed = () => {
      if (active?.video !== video) return;
      stop();
      entry.button.title = 'Preview unavailable. Try saving a new card turntable.';
    };
    video.addEventListener('error', failed);
    video.src = entry.url;
    video.play().then(paint).catch(failed);
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stop();
  });
  reducedMotion.addEventListener('change', () => {
    if (reducedMotion.matches && active?.hovered) stop();
    else if (reducedMotion.matches) clearHover();
  });

  return {
    stop,
    attach(card, image, job, name) {
      const revision = job.presentation?.library_turntable?.revision;
      if (job.state !== 'done' || revision == null) return;
      const media = document.createElement('span');
      media.className = 'card-turntable-media';
      image.before(media);
      media.append(image);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'card-turntable-play';
      function setButton(playing) {
        button.replaceChildren(icon(playing ? 'pause' : 'play'));
        button.setAttribute('aria-label', `${playing ? 'Stop' : 'Play'} turntable for ${name}`);
        button.setAttribute('aria-pressed', String(playing));
        button.title = playing ? 'Stop turntable' : 'Play turntable';
      }
      const url = `/api/jobs/${job.id}/presentation/library-turntable.webm?v=${encodeURIComponent(revision)}`;
      const entry = {card, media, button, setButton, url};
      setButton(false);
      const actions = document.createElement('div');
      actions.className = 'card-turntable-actions';
      const caption = card.querySelector('.reference-caption');
      const more = card.querySelector('.card-more');
      if (caption) actions.append(caption);
      actions.append(button);
      if (more) actions.append(more);
      card.append(actions);
      button.addEventListener('click', () => active?.card === card ? stop() : start(entry, false));
      card.addEventListener('pointerenter', event => {
        if (event.pointerType !== 'mouse' || !canHover.matches || reducedMotion.matches || active?.card === card) return;
        clearHover();
        hoverTimer = setTimeout(() => start(entry, true), 220);
      });
      card.addEventListener('pointerleave', () => {
        clearHover();
        if (active?.card === card && active.hovered) stop();
      });
    },
  };
}
