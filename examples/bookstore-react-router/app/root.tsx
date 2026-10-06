import { Outlet } from "react-router";

export default function Root() {
  return (
    <html>
      <body>
        <Outlet />
      </body>
    </html>
  );
}
