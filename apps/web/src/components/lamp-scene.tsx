import { useId } from "react";

export function LampScene() {
  const title = useId();
  return (
    <figure className="scene">
      <svg viewBox="0 0 480 410" role="img" aria-labelledby={title}>
        <title id={title}>
          Курьер передаёт упакованную фотолампу семье дома. Родители рядом с ребёнком; применение
          прибора не показано.
        </title>
        <rect x="8" y="14" width="464" height="374" rx="100" fill="var(--scene-bg)" />
        <g className="scene-sun">
          <circle cx="358" cy="91" r="38" fill="#f6d374" />
          <path
            d="M358 37v-8m54 62h8m-24-39 7-7m-7 85 7 7"
            stroke="#d6aa45"
            strokeWidth="4"
            strokeLinecap="round"
          />
        </g>
        <rect x="46" y="75" width="82" height="141" rx="30" fill="#fffdf8" />
        <path d="M87 76v139m-40-72h79" stroke="#d8e3da" strokeWidth="4" />
        <path d="M34 333h408" stroke="#9aa69e" strokeWidth="4" strokeLinecap="round" />
        <rect x="257" y="254" width="159" height="80" rx="24" fill="#a7c5bb" />
        <rect x="270" y="282" width="137" height="41" rx="15" fill="#bfd7cc" />
        <path
          d="M131 224l-9 107m30-107 12 107"
          stroke="#577880"
          strokeWidth="18"
          strokeLinecap="round"
        />
        <path d="M113 337h19m26 0h18" stroke="#344e54" strokeWidth="11" strokeLinecap="round" />
        <path d="M110 169q29-28 56 0l13 65h-79z" fill="#e4b86f" />
        <circle cx="137" cy="129" r="25" fill="#dfad8a" />
        <path d="M109 126q-2-41 43-31l16 28" fill="#577880" />
        <path d="M106 117h62" stroke="#577880" strokeWidth="8" strokeLinecap="round" />
        <circle cx="147" cy="129" r="2.5" fill="#344e54" />
        <path
          d="M146 141q-6 5-12 0"
          fill="none"
          stroke="#805849"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
        <path
          d="M112 183l35 42 47-16m-28-29 16 35"
          fill="none"
          stroke="#dfad8a"
          strokeWidth="13"
          strokeLinecap="round"
        />
        <path
          d="M297 211l-14 90m34-90 7 90"
          stroke="#678b7e"
          strokeWidth="18"
          strokeLinecap="round"
        />
        <path d="M277 162q27-23 51 0l12 74h-76z" fill="#729d8a" />
        <circle cx="304" cy="127" r="25" fill="#e6ba9a" />
        <path d="M278 126q-10-39 28-33 28-1 24 29l-12-13-26 5z" fill="#695447" />
        <circle cx="294" cy="128" r="2.5" fill="#344e54" />
        <path
          d="M296 140q7 5 13 0"
          fill="none"
          stroke="#805849"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
        <path
          d="M276 181l-28 31-35-4"
          fill="none"
          stroke="#e6ba9a"
          strokeWidth="13"
          strokeLinecap="round"
        />
        <path
          d="M358 250l-9 52m33-48 8 48"
          stroke="#bd8a6b"
          strokeWidth="15"
          strokeLinecap="round"
        />
        <path d="M342 202q20-24 44-2l19 73h-81z" fill="#d49474" />
        <path d="M342 176q-7-33 21-36 34-7 40 31l-6 44-61-4z" fill="#695447" />
        <circle cx="366" cy="173" r="23" fill="#edc7ac" />
        <path d="M343 172l-3-15q28-27 48 3l-29-5z" fill="#695447" />
        <circle cx="358" cy="174" r="2.5" fill="#344e54" />
        <path
          d="M360 184q7 5 12-1"
          fill="none"
          stroke="#805849"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
        <ellipse cx="347" cy="235" rx="34" ry="19" transform="rotate(-16 347 235)" fill="#fff6dd" />
        <circle cx="326" cy="231" r="11" fill="#edc7ac" />
        <path d="M323 230l4 1" stroke="#805849" strokeWidth="2" strokeLinecap="round" />
        <path
          d="M389 220l-20 30-39-3"
          fill="none"
          stroke="#edc7ac"
          strokeWidth="10"
          strokeLinecap="round"
        />
        <rect
          x="176"
          y="186"
          width="69"
          height="48"
          rx="10"
          fill="#f8eee0"
          stroke="#829994"
          strokeWidth="3"
        />
        <path d="M198 186v-10h26v10" fill="none" stroke="#829994" strokeWidth="4" />
        <circle cx="211" cy="210" r="9" fill="#e6be70" />
        <g className="scene-leaf">
          <path
            d="M434 330v-66m0 23q-34-2-28-30 28 4 28 30m0-8q27-3 21-28-22 6-21 28"
            fill="#729d8a"
          />
          <path d="M422 329h25l-5 18h-15z" fill="#d49474" />
        </g>
        <path d="M207 96l5-9 5 9 10 3-10 4-5 10-5-10-10-4z" fill="#e4b86f" />
      </svg>
      <figcaption className="scene-label">
        Сервис берёт на себя хлопоты.
        <br />
        Вы остаётесь рядом.
      </figcaption>
    </figure>
  );
}
