import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Safewalk",
  description: "Safe last-mile walking routes for MARTA riders"
};

export default function RootLayout({
  children
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
