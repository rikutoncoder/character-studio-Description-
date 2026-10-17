import './globals.css';
export const metadata = { title: 'Character Studio Workspace' };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ja"><body>{children}</body></html>;
}
