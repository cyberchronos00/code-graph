// tonic server and client (the generated `routeguide` module comes from `tonic::include_proto!`).
pub mod routeguide {
    tonic::include_proto!("routeguide");
}

use routeguide::route_guide_client::RouteGuideClient;
use routeguide::route_guide_server::RouteGuide;
use tonic::{Request, Response, Status};

pub struct Guide;

#[tonic::async_trait]
impl RouteGuide for Guide {
    async fn get_feature(&self, request: Request<routeguide::Point>) -> Result<Response<routeguide::Feature>, Status> {
        let _ = request;
        Err(Status::unimplemented("later"))
    }

    async fn record_route(&self, request: Request<tonic::Streaming<routeguide::Point>>) -> Result<Response<routeguide::RouteSummary>, Status> {
        let _ = request;
        Err(Status::unimplemented("later"))
    }
}

async fn ask() -> Result<(), Box<dyn std::error::Error>> {
    let mut client = RouteGuideClient::connect("http://[::1]:10000").await?;
    let _features = client.list_features(Request::new(routeguide::Rectangle::default())).await?;
    Ok(())
}

fn main() {
    let _ = ask();
}
