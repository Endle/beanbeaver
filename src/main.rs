use std::error::Error;

mod tui;

fn main() -> Result<(), Box<dyn Error>> {
    tui::run()
}
