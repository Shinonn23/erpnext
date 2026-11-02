Incoming quality inspection for received Items.

`Inspected By` defaults to the current authenticated user through the native `__user` token.
Both Desk and server-created documents resolve it before Link validation; it must never become a literal
User named `user`. This default supplies identity only and does not replace team, Item-assignment or workflow checks.
