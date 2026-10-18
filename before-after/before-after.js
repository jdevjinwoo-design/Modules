/*!
 * <before-after> — dependency-free before/after image comparison slider.
 *
 * Usage:
 *   load before-after.js with a <script defer> tag, then:
 *   <before-after before="a.jpg" after="b.jpg" orientation="horizontal"></before-after>
 *
 * Attributes:
 *   before / after        image URLs (required)
 *   before-alt/after-alt  alt text
 *   before-label/after-label  corner labels (default "Before"/"After"; "" hides)
 *   orientation           "horizontal" (left/right, default) | "vertical" (top/bottom)
 *   value                 initial divider position 0–100 (default 50)
 *   hover                 present → divider follows the pointer without clicking
 *   ratio                 fixed aspect ratio, e.g. "16/9" (default: the after image's natural ratio)
 *
 * CSS custom properties (set on the element):
 *   --ba-line-color, --ba-line-width, --ba-handle-size, --ba-handle-bg,
 *   --ba-handle-color, --ba-label-bg, --ba-label-color, --ba-radius
 *
 * Events: "input" while dragging, "change" when released; event.detail.value.
 */
(() => {
  if (customElements.get('before-after')) return;

  const template = document.createElement('template');
  template.innerHTML = `
<style>
  :host {
    --ba-line-color: #fff;
    --ba-line-width: 2px;
    --ba-handle-size: 44px;
    --ba-handle-bg: rgba(255,255,255,.95);
    --ba-handle-color: #222;
    --ba-label-bg: rgba(0,0,0,.55);
    --ba-label-color: #fff;
    --ba-radius: 0px;
    --pos: 50%;
    display: block;
    position: relative;
    overflow: hidden;
    border-radius: var(--ba-radius);
    user-select: none;
    -webkit-user-select: none;
    touch-action: pan-y;
    cursor: ew-resize;
    line-height: 0;
    background: #ddd;
  }
  :host([orientation="vertical"]) { touch-action: pan-x; cursor: ns-resize; }
  :host([hidden]) { display: none; }
  :host(:focus-visible) { outline: 3px solid #4c9ffe; outline-offset: 2px; }

  img { display: block; width: 100%; height: auto; pointer-events: none; -webkit-user-drag: none; }
  :host([ratio]) .base, .over { height: 100%; object-fit: cover; }
  :host([ratio]) .base { position: absolute; inset: 0; }
  .over {
    position: absolute; inset: 0;
    clip-path: inset(0 calc(100% - var(--pos)) 0 0);
  }
  :host([orientation="vertical"]) .over { clip-path: inset(0 0 calc(100% - var(--pos)) 0); }

  .line {
    position: absolute; top: 0; bottom: 0; left: var(--pos);
    width: var(--ba-line-width); transform: translateX(-50%);
    background: var(--ba-line-color);
    box-shadow: 0 0 6px rgba(0,0,0,.35);
    pointer-events: none;
  }
  :host([orientation="vertical"]) .line {
    top: var(--pos); bottom: auto; left: 0; right: 0;
    width: auto; height: var(--ba-line-width); transform: translateY(-50%);
  }
  .handle {
    position: absolute; top: 50%; left: 50%;
    width: var(--ba-handle-size); height: var(--ba-handle-size);
    transform: translate(-50%, -50%);
    border-radius: 50%;
    background: var(--ba-handle-bg); color: var(--ba-handle-color);
    box-shadow: 0 2px 8px rgba(0,0,0,.35);
    display: grid; place-items: center;
  }
  .handle svg { width: 60%; height: 60%; }
  :host([orientation="vertical"]) .handle svg { transform: rotate(90deg); }

  .label {
    position: absolute; top: 12px;
    padding: 6px 10px; border-radius: 4px;
    font: 600 12px/1 system-ui, -apple-system, sans-serif;
    letter-spacing: .04em; text-transform: uppercase;
    background: var(--ba-label-bg); color: var(--ba-label-color);
    pointer-events: none; transition: opacity .2s;
  }
  .label:empty { display: none; }
  .label.before { left: 12px; }
  .label.after { right: 12px; }
  :host([orientation="vertical"]) .label.after { top: auto; bottom: 12px; left: 12px; right: auto; }
</style>
<img class="base" part="after-image" alt="">
<img class="over" part="before-image" alt="">
<span class="label before" part="label before-label"></span>
<span class="label after" part="label after-label"></span>
<div class="line" part="line">
  <div class="handle" part="handle">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <polyline points="9 6 3 12 9 18"></polyline><polyline points="15 6 21 12 15 18"></polyline>
    </svg>
  </div>
</div>`;

  const clamp = (n) => Math.min(100, Math.max(0, n));

  class BeforeAfter extends HTMLElement {
    static get observedAttributes() {
      return ['before', 'after', 'before-alt', 'after-alt', 'before-label', 'after-label',
              'orientation', 'value', 'ratio'];
    }

    constructor() {
      super();
      const root = this.attachShadow({ mode: 'open' });
      root.appendChild(template.content.cloneNode(true));
      this._base = root.querySelector('.base');
      this._over = root.querySelector('.over');
      this._labelBefore = root.querySelector('.label.before');
      this._labelAfter = root.querySelector('.label.after');
      this._value = 50;
      this._dragging = false;

      this._onDown = this._onDown.bind(this);
      this._onMove = this._onMove.bind(this);
      this._onUp = this._onUp.bind(this);
      this._onKey = this._onKey.bind(this);
    }

    connectedCallback() {
      if (!this.hasAttribute('tabindex')) this.tabIndex = 0;
      this.setAttribute('role', 'slider');
      this.setAttribute('aria-valuemin', '0');
      this.setAttribute('aria-valuemax', '100');
      if (!this.hasAttribute('aria-label')) this.setAttribute('aria-label', 'Before/after comparison');
      this.addEventListener('pointerdown', this._onDown);
      this.addEventListener('pointermove', this._onMove);
      this.addEventListener('pointerup', this._onUp);
      this.addEventListener('pointercancel', this._onUp);
      this.addEventListener('keydown', this._onKey);
      this._render();
    }

    disconnectedCallback() {
      this.removeEventListener('pointerdown', this._onDown);
      this.removeEventListener('pointermove', this._onMove);
      this.removeEventListener('pointerup', this._onUp);
      this.removeEventListener('pointercancel', this._onUp);
      this.removeEventListener('keydown', this._onKey);
    }

    attributeChangedCallback(name) {
      if (name === 'value') {
        const v = parseFloat(this.getAttribute('value'));
        if (!Number.isNaN(v)) this._setValue(v, false);
        return;
      }
      this._render();
    }

    get value() { return this._value; }
    set value(v) { this._setValue(Number(v), false); }

    get orientation() { return this.getAttribute('orientation') === 'vertical' ? 'vertical' : 'horizontal'; }
    set orientation(v) { this.setAttribute('orientation', v); }

    _render() {
      const attr = (n, d = '') => (this.hasAttribute(n) ? this.getAttribute(n) : d);
      // "after" is the full base layer; "before" is clipped on top of it.
      if (this._base.getAttribute('src') !== attr('after')) this._base.src = attr('after');
      if (this._over.getAttribute('src') !== attr('before')) this._over.src = attr('before');
      this._base.alt = attr('after-alt', 'After');
      this._over.alt = attr('before-alt', 'Before');
      this._labelBefore.textContent = attr('before-label', 'Before');
      this._labelAfter.textContent = attr('after-label', 'After');
      this.style.aspectRatio = attr('ratio');
      this.setAttribute('aria-orientation', this.orientation);
      this._paint();
    }

    _setValue(v, emit) {
      if (Number.isNaN(v)) return;
      v = clamp(v);
      if (v === this._value) return;
      this._value = v;
      this._paint();
      if (emit) this.dispatchEvent(new CustomEvent('input', { detail: { value: v }, bubbles: true }));
    }

    _paint() {
      this.style.setProperty('--pos', this._value + '%');
      this.setAttribute('aria-valuenow', String(Math.round(this._value)));
      // Fade a label out when the divider covers it.
      this._labelBefore.style.opacity = this._value < 15 ? '0' : '';
      this._labelAfter.style.opacity = this._value > 85 ? '0' : '';
    }

    _fromEvent(e) {
      const r = this.getBoundingClientRect();
      return this.orientation === 'vertical'
        ? ((e.clientY - r.top) / r.height) * 100
        : ((e.clientX - r.left) / r.width) * 100;
    }

    _emitChange() {
      this.dispatchEvent(new CustomEvent('change', { detail: { value: this._value }, bubbles: true }));
    }

    _onDown(e) {
      if (e.button !== 0) return;
      this._dragging = true;
      this.setPointerCapture(e.pointerId);
      this._setValue(this._fromEvent(e), true);
    }

    _onMove(e) {
      if (this._dragging || (this.hasAttribute('hover') && e.pointerType === 'mouse')) {
        this._setValue(this._fromEvent(e), true);
      }
    }

    _onUp(e) {
      if (!this._dragging) return;
      this._dragging = false;
      if (this.hasPointerCapture(e.pointerId)) this.releasePointerCapture(e.pointerId);
      this._emitChange();
    }

    _onKey(e) {
      const step = e.shiftKey ? 10 : 1;
      const keys = {
        ArrowLeft: -step, ArrowDown: -step, ArrowRight: step, ArrowUp: step,
        PageDown: -10, PageUp: 10, Home: -Infinity, End: Infinity,
      };
      if (!(e.key in keys)) return;
      e.preventDefault();
      // In vertical mode, ArrowDown moves the divider down (towards "after").
      let d = keys[e.key];
      if (this.orientation === 'vertical' && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) d = -d;
      this._setValue(this._value + d, true);
      this._emitChange();
    }
  }

  customElements.define('before-after', BeforeAfter);
})();
