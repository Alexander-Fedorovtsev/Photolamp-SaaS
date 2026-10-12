import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Photolamp",
    short_name: "Photolamp",
    lang: "ru",
    start_url: "/",
    display: "standalone",
    background_color: "#fcfaf5",
    theme_color: "#fcfaf5",
    icons: [{ src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" }],
  };
}
