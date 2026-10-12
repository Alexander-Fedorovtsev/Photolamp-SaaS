export function LampScene() {
  return (
    <div className="scene">
      <svg viewBox="0 0 480 400" role="img" aria-labelledby="lamp-title">
        <title id="lamp-title">Стилизованная фотолампа в спокойном домашнем интерьере</title>
        <rect x="38" y="30" width="404" height="340" rx="80" fill="var(--scene-bg)" />
        <circle cx="365" cy="98" r="32" fill="#f6d374" />
        <path d="M70 312h340" stroke="#9aa69e" strokeWidth="5" strokeLinecap="round" />
        <rect x="86" y="106" width="90" height="128" rx="32" fill="#fffdf8" />
        <path d="M131 106v128m-45-64h90" stroke="#e7ece5" strokeWidth="5" />
        <path
          d="M313 310v-70m0 20c-40 0-50-35-29-51 24 3 29 29 29 51m0-9c39 0 46-36 26-50-23 4-26 26-26 50"
          fill="#7b9c80"
        />
        <path d="M212 306h70m-35 0V173" stroke="#677d87" strokeWidth="10" strokeLinecap="round" />
        <rect x="187" y="150" width="120" height="46" rx="22" fill="#6b95a0" />
        <rect x="198" y="164" width="98" height="19" rx="9" fill="#d9e6e8" />
        <rect x="90" y="272" width="75" height="39" rx="12" fill="#edc6a9" />
        <path d="M108 272v-15h38v15" fill="none" stroke="#bd9172" strokeWidth="5" />
      </svg>
      <p className="scene-label">
        Меньше организационных забот.
        <br />
        Больше времени рядом.
      </p>
    </div>
  );
}
