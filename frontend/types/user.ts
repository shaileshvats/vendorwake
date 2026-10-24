/** The subset of the Auth0 session user the UI renders. */
export interface SessionUser {
  sub: string;
  name: string | null;
  email: string | null;
  picture: string | null;
}
