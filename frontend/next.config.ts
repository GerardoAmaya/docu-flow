import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // El navegador pide las imagenes de pagina directo a la API, asi que no
  // hace falta proxy; solo la URL base como variable de entorno.
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000",
  },
};

export default nextConfig;
